"""Steam 리뷰 수집과 표본 크기 계획.

절차:
1. Steam query_summary로 한국어 모집단 N, 추천/비추천 비율 확인
2. Cochran 공식으로 목표 오차한계에 맞는 최소 표본 크기 n 계산
3. 부정 리뷰가 약 MIN_NEG건(기대값) 들어오도록 n을 조정. 무작위로 뽑으므로 실제 건수는 조금 다를 수 있다
4. 최신순·공감순이면 모집단 추천 비율에 맞춰 추천/비추천을 할당 수집한다.
   이 방식은 무작위 추출이나 전체 기간 대표성을 보장하지 않는다.
5. 하나의 reviews.csv로 저장

결과:
- reviews.csv  (수집된 리뷰)
- sample_design.json  (표본 설계 기록)
"""

import csv
import json
import math
import os
import time
from datetime import datetime, timezone
import httpx

from config import cfg
import progress
import sampling

# ─── 설정 ──────────────────────────────────────────────────────────────
def get_app_id(): return cfg.APP_ID
def get_lang(): return cfg.LANG
def get_target_error(): return cfg.TARGET_ERROR_PCT
def get_min_neg(): return cfg.MIN_NEG_REVIEWS
def get_out_csv(): return cfg.REVIEWS_CSV
def get_out_json(): return cfg.SAMPLE_JSON
def get_url(): return cfg.STEAM_API_URL
def get_sort():
    value = getattr(cfg, "COLLECT_SORT", "recent")
    return value if value in ("helpful", "random") else "recent"

RANDOM_SEED = 42            # 같은 범위를 다시 뽑으면 같은 리뷰가 나온다
RANDOM_POOL_MAX = 60000     # 무작위로 뽑기 전에 훑는 리뷰 수의 한도 (약 20분)
def get_since_ts():
    since = getattr(cfg, "COLLECT_SINCE", None)
    if not since:
        return None
    return int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

# ─── 1단계: 모집단 조회 ───────────────────────────────────────────────

def fetch_population():
    """Steam query_summary에서 한국어 리뷰 모집단 통계를 가져온다."""
    r = httpx.get(get_url().format(appid=get_app_id()), params={
        "json": 1, "filter": "recent", "language": get_lang(),
        "review_type": "all", "purchase_type": "all",
        "num_per_page": 0, "filter_offtopic_activity": 0,
    }, timeout=30.0)
    r.raise_for_status()
    qs = r.json().get("query_summary", {})
    total = qs.get("total_reviews", 0)
    positive = qs.get("total_positive", 0)
    negative = qs.get("total_negative", 0)
    score = qs.get("review_score_desc", "")
    if total == 0:
        raise ValueError("모집단 조회 실패: total_reviews=0")
    return {
        "total": total,
        "positive": positive,
        "negative": negative,
        "pos_rate": positive / total,
        "neg_rate": negative / total,
        "score": score,
    }


# ─── 2단계: 표본 크기 계산 ─────────────────────────────────────────────

def decide_sample_size(pop):
    """지금 설정(목표 오차 · 비추천 목표 건수 · 직접 지정)으로 수집할 건수를 정한다. 식은 sampling.py에 있다."""
    custom = getattr(cfg, "CUSTOM_SAMPLE_SIZE", None)
    plan = sampling.plan_sample_size(pop["total"], pop["negative"], get_target_error(), get_min_neg(),
                                     custom if custom and custom > 0 else None)
    reason = (f"직접 지정한 {plan['n_total']}건" if custom and custom > 0
              else f"오차 ±{get_target_error()}%에 {plan['n_by_error']}건, 비추천 약 {get_min_neg()}건(기대값)에 {plan['n_for_neg']}건 → {plan['n_total']}건")
    return {"n_total": plan["n_total"], "n_pos": plan["n_pos"], "n_neg": plan["n_neg"], "reason": reason}

# ─── 3단계: 수집 ──────────────────────────────────────────────────────

# Steam은 짧은 시간에 많이 물으면 429(요청이 너무 많음)로 거절한다. 무작위는 수백 쪽을 이어서 받으므로
# 천천히 묻고, 거절당하면 기다렸다가 같은 쪽부터 다시 묻는다. 기다림을 다 쓰면 멈춘다.
RATE_WAITS = (30, 60, 120, 180, 300)
PAGE_INTERVAL = {"random": 1.5}     # 쪽 사이 쉬는 시간(초). 그 밖의 방법은 몇십 쪽이라 0.6초


def steam_page(params):
    """Steam 리뷰 한 쪽을 받는다."""
    for wait in RATE_WAITS + (None,):
        r = httpx.get(get_url().format(appid=get_app_id()), params=params, timeout=30.0)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
        if wait is not None:
            print(f"  Steam이 요청이 많다고 합니다. {wait}초 기다렸다가 이어서 받습니다.")
            time.sleep(wait)
    raise ValueError("Steam이 요청을 계속 거절합니다(요청이 너무 많음). 10분쯤 뒤에 같은 설정으로 다시 실행하면 받은 데부터 이어서 모읍니다")


class ScanCheckpoint:
    """훑은 데까지의 기록. 수집이 멈춰도 남아 있다가, 같은 범위로 다시 실행하면 그 자리부터 이어서 훑는다.

    scan_rows.jsonl에 받은 리뷰를 한 줄씩 덧붙이고, scan_state.json에 범위·다음 쪽 위치·건수를 적는다.
    수집이 끝나 reviews.csv가 만들어지면 두 파일을 지운다.
    """
    MAX_AGE = 24 * 3600   # 하루가 지난 기록은 버린다. 그사이 새 리뷰가 많이 쌓였을 수 있다

    def __init__(self, folder, scope):
        self.rows_path = os.path.join(folder, "scan_rows.jsonl")
        self.state_path = os.path.join(folder, "scan_state.json")
        self.scope = scope
        self.rows, self.cursor = [], "*"

    def load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as stream:
                state = json.load(stream)
            if state.get("scope") != self.scope or time.time() - state.get("saved_at", 0) > self.MAX_AGE:
                return False
            with open(self.rows_path, "r", encoding="utf-8") as stream:
                rows = [json.loads(line) for line in stream if line.strip()]
        except (OSError, ValueError):
            return False
        if len(rows) < state.get("count", 0) or not state.get("cursor"):
            return False
        self.rows, self.cursor = rows[:state["count"]], state["cursor"]   # 쓰다 만 줄은 버린다
        return bool(self.rows)

    def save(self, new_rows, cursor, count):
        with open(self.rows_path, "a" if count > len(new_rows) else "w", encoding="utf-8") as stream:
            for row in new_rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        with open(self.state_path + ".tmp", "w", encoding="utf-8") as stream:
            json.dump({"scope": self.scope, "cursor": cursor, "count": count, "saved_at": time.time()}, stream, ensure_ascii=False)
        os.replace(self.state_path + ".tmp", self.state_path)

    def clear(self):
        for path in (self.rows_path, self.state_path):
            if os.path.exists(path):
                os.remove(path)


def collect_reviews(review_type, target, existing_ids, base=0, total=None, scan_status=None, checkpoint=None):
    """review_type별로 target건 수집. scan_status는 끝까지 훑었는지 기록한다.
    checkpoint를 주면 쪽마다 훑은 데까지를 남기고, 남아 있는 기록이 있으면 거기서부터 이어서 받는다."""
    collected = list(checkpoint.rows) if checkpoint else []
    cursor = checkpoint.cursor if checkpoint else "*"
    existing_ids.update(row["recommendationid"] for row in collected)
    since_ts = get_since_ts()
    params = {"json": 1, "filter": "recent", "language": get_lang(),
              "review_type": review_type, "purchase_type": "all",
              "num_per_page": 100, "filter_offtopic_activity": 0}
    if get_sort() == "helpful":
        # 공감순은 Steam의 'all' 정렬이다. 기간은 최대 365일까지만 걸 수 있다.
        params["filter"] = "all"
        if since_ts:
            days = math.ceil((time.time() - since_ts) / 86400)
            if days > 365:
                raise ValueError("공감순 수집은 최근 365일 이내 기간만 지원합니다. 시작 날짜를 바꾸세요")
            params["day_range"] = max(1, days)
    while len(collected) < target:
        data = steam_page({**params, "cursor": cursor})
        reviews = data.get("reviews", [])
        if not reviews:
            if scan_status is not None:
                scan_status["complete"] = True
            print(f"  [{review_type}] 더 이상 리뷰 없음. 중단.")
            break
        new_count = 0
        older = 0
        page_start = len(collected)
        for rv in reviews:
            rid = str(rv.get("recommendationid"))
            if since_ts and int(rv.get("timestamp_created") or 0) < since_ts:
                older += 1
                continue
            if rid in existing_ids:
                continue
            existing_ids.add(rid)
            author = rv.get("author", {})
            collected.append({
                "recommendationid": rid,
                "content": rv.get("review", "").replace("\r", " ").replace("\n", " ").strip(),
                "language": rv.get("language", get_lang()),
                "voted_up": 1 if rv.get("voted_up") else 0,
                "votes_up": rv.get("votes_up", 0),
                "votes_funny": rv.get("votes_funny", 0),
                "weighted_vote_score": rv.get("weighted_vote_score", "0"),
                "playtime_at_review_min": author.get("playtime_at_review"),
                "playtime_forever_min": author.get("playtime_forever", 0),
                "author_steamid": author.get("steamid", ""),
                "timestamp_created": rv.get("timestamp_created", 0),
            })
            new_count += 1
            if len(collected) >= target:
                break
        print(f"  [{review_type}] {len(collected)}/{target} (이번 페이지 +{new_count})")
        progress.report("collect", base + len(collected), total or target)
        if since_ts and get_sort() in ("recent", "random") and older == len(reviews):
            if scan_status is not None:
                scan_status["complete"] = True
            print(f"  [{review_type}] {cfg.COLLECT_SINCE} 이전 리뷰에 도달. 중단.")
            break
        next_cursor = data.get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        if checkpoint:
            checkpoint.save(collected[page_start:], cursor, len(collected))
        time.sleep(PAGE_INTERVAL.get(get_sort(), 0.6))
    return collected


# ─── 메인 ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("📊 Steam 리뷰 수집과 표본 크기 계획")
    print("=" * 60)

    # 1) 모집단 조회
    print("\n[1] Steam 모집단 조회…")
    pop = fetch_population()
    print(f"  전체: {pop['total']:,}건")
    print(f"  긍정: {pop['positive']:,}건 ({pop['pos_rate']*100:.1f}%)")
    print(f"  부정: {pop['negative']:,}건 ({pop['neg_rate']*100:.1f}%)")
    print(f"  Steam 평가: {pop['score']}")

    # 2) 표본 크기 결정
    print("\n[2] 표본 크기 계산…")
    design = decide_sample_size(pop)
    print(f"  목표: 오차 ±{get_target_error()}% + 부정 최소 {get_min_neg()}건")
    print(f"  결정: 총 {design['n_total']}건 (긍정 {design['n_pos']} / 부정 {design['n_neg']})")
    print(f"  사유: {design['reason']}")

    # 3) 수집
    print("\n[3] 리뷰 수집 시작…")
    existing = []
    if getattr(cfg, "INCREMENTAL", False) and os.path.exists(get_out_csv()):
        with open(get_out_csv(), encoding="utf-8-sig", newline="") as stream:
            existing = list(csv.DictReader(stream))
    seen_ids = {str(row["recommendationid"]) for row in existing}

    total = design["n_pos"] + design["n_neg"]
    random_pool = None
    if get_sort() == "random":
        # 무작위: 범위 안의 리뷰를 끝까지 훑은 다음 그중에서 뽑는다. 추천·비추천을 따로 맞추지 않아도
        # 무작위로 뽑으면 범위 안의 비율이 그대로 따라온다.
        if existing:
            raise ValueError("무작위 수집은 기존 리뷰에 이어서 할 수 없습니다. 새 분석으로 시작하세요")
        import random
        print(f"\n  ── 범위 안의 리뷰를 모두 훑습니다 (최대 {RANDOM_POOL_MAX:,}건) ──")
        progress.report("collect", 0, min(pop["total"], RANDOM_POOL_MAX), force=True)
        scan_status = {"complete": False}
        checkpoint = ScanCheckpoint(cfg.project_dir(), {"app_id": get_app_id(), "language": get_lang(),
                                                        "since": getattr(cfg, "COLLECT_SINCE", None)})
        if checkpoint.load():
            print(f"  지난번에 훑은 {len(checkpoint.rows):,}건에서 이어서 받습니다")
        pool = collect_reviews("all", RANDOM_POOL_MAX, seen_ids, 0,
                               min(pop["total"], RANDOM_POOL_MAX), scan_status=scan_status, checkpoint=checkpoint)
        # 전체 기간이면 Steam 모집단 건수와도 대조한다. API가 일찍 빈 페이지를 주는 경우를 완주로 오인하지 않는다.
        # Steam이 알려 주는 전체 건수에는 지워졌거나 가려져 받을 수 없는 글이 조금 섞여 있어 2%까지는 완주로 본다.
        complete = scan_status["complete"] and (get_since_ts() is not None or len(pool) >= pop["total"] * .98)
        random_pool = {"size": len(pool), "complete": complete, "seed": RANDOM_SEED}
        picked = random.Random(RANDOM_SEED).sample(pool, min(total, len(pool)))
        pos_reviews = [r for r in picked if r["voted_up"]]
        neg_reviews = [r for r in picked if not r["voted_up"]]
        print(f"  훑은 리뷰 {len(pool):,}건 중 {len(picked):,}건을 무작위로 뽑았습니다")
    else:
        print(f"\n  ── 긍정 리뷰 {design['n_pos']}건 수집 ──")
        progress.report("collect", 0, total, force=True)
        pos_reviews = collect_reviews("positive", design["n_pos"], seen_ids, 0, total)

        print(f"\n  ── 부정 리뷰 {design['n_neg']}건 수집 ──")
        neg_reviews = collect_reviews("negative", design["n_neg"], seen_ids, len(pos_reviews), total)

    all_reviews = existing + pos_reviews + neg_reviews

    # 4) CSV 저장
    if not all_reviews:
        print("❌ 수집된 리뷰가 없습니다.")
        return

    fieldnames = list(dict.fromkeys(key for row in all_reviews for key in row.keys()))
    with open(get_out_csv(), "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_reviews)

    # 5) 설계 기록 저장
    actual_pos = sum(1 for r in all_reviews if str(r["voted_up"]).lower() in ("1", "true"))
    actual_neg = len(all_reviews) - actual_pos
    actual_total = len(all_reviews)

    record = {
        "population": pop,
        "design": design,
        "actual": {
            "total": actual_total,
            "positive": actual_pos,
            "negative": actual_neg,
            "pos_rate": round(actual_pos / actual_total, 4) if actual_total else 0,
            "neg_rate": round(actual_neg / actual_total, 4) if actual_total else 0,
        },
        "params": {
            "target_error_pct": get_target_error(),
            "custom_sample_size": getattr(cfg, "CUSTOM_SAMPLE_SIZE", None),
            "min_neg": get_min_neg(),
            "confidence_level": 0.95,
            "language": get_lang(),
            "app_id": get_app_id(),
            "since": getattr(cfg, "COLLECT_SINCE", None),
            "sort": get_sort(),
            "min_len": cfg.MIN_REVIEW_LEN,   # 이보다 짧은 글만 AI 분석에서 뺀다
            "random_pool": random_pool,      # 무작위 수집일 때: 훑은 리뷰 수, 끝까지 훑었는지, 씨앗
        },
    }
    with open(get_out_json(), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    if random_pool is not None:
        checkpoint.clear()   # 리뷰 파일이 만들어졌으니 훑던 기록은 필요 없다

    # 6) 요약
    print(f"\n{'='*60}")
    print(f"✅ 수집 완료!")
    print(f"  총 {actual_total}건 → {get_out_csv()}")
    print(f"  긍정: {actual_pos}건 ({actual_pos/actual_total*100:.1f}%)")
    print(f"  부정: {actual_neg}건 ({actual_neg/actual_total*100:.1f}%)")
    print(f"  설계 기록: {get_out_json()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
