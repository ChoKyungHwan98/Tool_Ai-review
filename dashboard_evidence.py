"""Read-only evidence for the visual review dashboard. No AI/API calls.

Counts describe collected reviews, not all players. Topic sentiment and Steam
recommendation are different dimensions; one review may contain both sentiments.
"""
import csv
import json
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import analysis_design
import sampling

BUCKETS = ((0, 2, "2시간 미만"), (2, 20, "2~20시간"),
           (20, 100, "20~100시간"), (100, float("inf"), "100시간 이상"))
FUN = {"감각": "보고 듣는 즐거움", "판타지": "다른 존재가 되는 경험",
       "이야기": "줄거리와 전개", "도전": "어려움을 넘는 성취",
       "함께": "다른 사람과 어울림", "발견": "탐험과 새로운 발견",
       "표현": "꾸미고 나를 드러냄", "몰두": "시간 가는 줄 모르는 반복"}


def number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def review_hours(row):
    # Zero is a valid value. Current total hours cannot stand in for hours at review.
    minutes = number(row.get("playtime_at_review_min"))
    return minutes / 60 if minutes is not None and minutes >= 0 else None


def bucket_index(row):
    hours = review_hours(row)
    return next((i for i, (lo, hi, _) in enumerate(BUCKETS)
                 if hours is not None and lo <= hours < hi), None)


def is_positive(row):
    return str(row.get("voted_up", "")).lower() in ("1", "true")


def load_sources(directory):
    folder = Path(directory)
    names = ("reviews.csv", "analysis_v3.jsonl", "sample_design.json", "complaints_v3.jsonl")
    stamps = tuple((folder / name).stat().st_mtime_ns if (folder / name).exists() else None
                   for name in names)
    return _load_sources(str(folder), stamps)


@lru_cache(maxsize=8)
def _load_sources(directory, stamps):
    folder = Path(directory)
    if not (folder / "reviews.csv").exists() or not (folder / "analysis_v3.jsonl").exists():
        return None
    with (folder / "reviews.csv").open(encoding="utf-8-sig", newline="") as stream:
        reviews = {str(r["recommendationid"]): r for r in csv.DictReader(stream)}
    analyzed = {}
    skipped = 0
    with (folder / "analysis_v3.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(item, dict) or str(item.get("id")) not in reviews:
                skipped += 1
                continue
            analyzed[str(item["id"])] = item
    design = {}
    if (folder / "sample_design.json").exists():
        with (folder / "sample_design.json").open(encoding="utf-8") as stream:
            design = json.load(stream)
    complaints = []
    if (folder / "complaints_v3.jsonl").exists():
        with (folder / "complaints_v3.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line) if line.strip() else None
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and str(item.get("id")) in reviews:
                    complaints.append(item)
    return reviews, analyzed, design, skipped, complaints


def topic_members(analyzed):
    """AI가 붙인 주제별 칭찬·불만 리뷰 번호."""
    members = {}
    for rid, item in analyzed.items():
        for pair in item.get("t") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            name, sentiment = pair
            if not isinstance(name, str) or name == "기타" or sentiment not in ("P", "N"):
                continue
            group = members.setdefault(name, {"P": set(), "N": set()})
            group[sentiment].add(rid)
    return members


def merged_topics(analyzed):
    """AI 주제에 자동 합치기 규칙을 적용한다. (주제별 리뷰, alias, 합친 기록, AI가 낸 주제 수)"""
    raw = topic_members(analyzed)
    alias, merges = analysis_design.auto_merge(raw)
    return analysis_design.merge_members(raw, alias), alias, merges, len(raw)


def excerpt(row, app_id, limit=260):
    content = row.get("content") or ""
    author = str(row.get("author_steamid") or "")
    return {"id": str(row["recommendationid"]), "content": content[:limit],
            "truncated": len(content) > limit, "recommended": is_positive(row),
            "hours": review_hours(row), "helpful": int(number(row.get("votes_up")) or 0),
            "url": f"https://steamcommunity.com/profiles/{author}/recommended/{int(app_id)}/"
            if author.isdigit() else None}


def sorted_ids(ids, reviews):
    return sorted(ids, key=lambda rid: (-(number(reviews[rid].get("votes_up")) or 0),
                                       -(number(reviews[rid].get("timestamp_created")) or 0), rid))


def min_stamp(reviews, positive):
    """추천(또는 비추천) 리뷰 중 가장 오래된 글의 작성 시각."""
    return min(t for t in (number(r.get("timestamp_created")) for r in reviews.values() if is_positive(r) == positive)
               if t is not None and t > 0)


def eligible_ids(reviews, design):
    """AI 분석 대상인 리뷰 번호: 수집할 때 기록한 최소 글자 수(기본 2) 이상인 글."""
    min_len = (design.get("params") or {}).get("min_len") or 2
    return {rid for rid, r in reviews.items() if len((r.get("content") or "").strip()) >= min_len}

def promise_check(design, collected):
    """표본 오차를 말할 수 있는지 수집 방법으로 따진다.

    이 오차는 추천 비율에 대한 것이고, 추천 여부는 수집한 리뷰 전부에 있다. 그래서 AI 분석이 몇 건 빠졌는지는
    조건이 아니다(빠진 건수는 '수집에서 분석까지' 카드가 따로 보여 준다)."""
    params, population = design.get("params") or {}, (design.get("population") or {}).get("total")
    planned, target = (design.get("design") or {}).get("n_total"), params.get("target_error_pct")
    if not planned or target is None:
        return None
    pool = params.get("random_pool") or {}
    random_complete = params.get("sort") == "random" and pool.get("complete") is True
    # 기간을 지정했거나 Steam의 전체 건수가 변했을 수 있으므로 실제로 끝까지 훑은 범위를 분모로 쓴다.
    frame_size = pool.get("size") if random_complete else population
    enough = bool(frame_size) and collected >= min(planned, frame_size)
    actual_margin = sampling.margin_of_error(collected, frame_size) if random_complete else None
    checks = [
        {"name": "수집 범위를 끝까지 훑고 무작위로 뽑았는가", "ok": random_complete,
         "text": ("범위 안의 리뷰를 모두 훑고 그중에서 뽑았습니다" if params.get("sort") == "random" and pool.get("complete")
                  else "리뷰가 많아 최근 글 안에서만 뽑았습니다" if params.get("sort") == "random"
                  else "공감이 많은 글부터 가져왔습니다" if params.get("sort") == "helpful"
                  else "가장 최근 글부터 차례로 가져왔습니다. 계획한 건수가 차면 그보다 오래된 리뷰는 들어오지 않습니다")},
        {"name": "계획한 만큼 모았는가", "ok": enough,
         "text": f"계획 {planned:,}건 중 {collected:,}건"},
        {"name": "고른 오차 이내인가", "ok": actual_margin is not None and actual_margin <= target,
         "text": f"실제 수집 건수 기준 최대 ±{round(actual_margin, 1)}%p" if actual_margin is not None else "무작위 수집 범위가 확인되지 않음"},
    ]
    kept = all(c["ok"] for c in checks)
    return {"target": target, "planned": planned, "population": population, "checks": checks,
            "kept": kept,
            "analyzed_margin": round(actual_margin, 1) if kept else None}


def stage_profile(key, name, ids, reviews):
    """한 단계에 남은 리뷰 묶음의 성격: 건수 · 비추천 비율 · 플레이 시간과 글 길이의 가운데 값."""
    rows = [reviews[rid] for rid in ids]
    if not rows:
        return None
    middle = lambda values: statistics.median(values) if values else None
    hours = sorted(h for h in (review_hours(r) for r in rows) if h is not None)
    lengths = sorted(len((r.get("content") or "").strip()) for r in rows)
    return {"key": key, "name": name, "n": len(rows),
            "negative_rate": round(sum(not is_positive(r) for r in rows) / len(rows) * 100, 1),
            "hours": round(middle(hours), 1) if hours else None, "length": middle(lengths)}


def build_evidence(directory, app_id):
    source = load_sources(directory)
    if source is None:
        return None
    reviews, analyzed, design, skipped, complaints = source
    members, alias, merges, n_ai_topics = merged_topics(analyzed)
    n = len(reviews)
    eligible = eligible_ids(reviews, design)
    up = sum(is_positive(r) for r in reviews.values())
    cohorts = []
    for index, (_, _, label) in enumerate(BUCKETS):
        ids = {rid for rid, row in reviews.items() if bucket_index(row) == index}
        analyzed_ids = ids & analyzed.keys()
        negative = sum(not is_positive(reviews[rid]) for rid in ids)
        cohorts.append({"label": label, "n": len(ids), "analyzed": len(analyzed_ids),
                        "negative": negative, "negative_rate": round(negative / len(ids) * 100, 1) if ids else None,
                        "small": len(ids) < 30})
    themes = []
    for name, group in members.items():
        ids = group["P"] | group["N"]
        # 화면의 불만 비율과 같은 분모(칭찬 언급 + 불만 언급)로 범위를 구한다.
        neg_range = sampling.wilson_interval(len(group["N"]), len(group["P"]) + len(group["N"]))
        cells = []
        for index, cohort in enumerate(cohorts):
            count = sum(bucket_index(reviews[rid]) == index for rid in group["N"])
            cells.append({"count": count, "denominator": cohort["analyzed"],
                          "rate": round(count / cohort["analyzed"] * 100, 1) if cohort["analyzed"] else None})
        themes.append({"name": name, "pos": len(group["P"]), "neg": len(group["N"]),
                       "mentions": len(ids),
                       # 범위가 50%를 걸치면 이 건수로는 칭찬이 많은지 불만이 많은지 말할 수 없다.
                       "neg_range": neg_range,
                       "sure": bool(neg_range) and (neg_range[0] > 50 or neg_range[1] < 50),
                       "negative_recommended": sum(is_positive(reviews[rid]) for rid in group["N"]),
                       "cells": cells,
                       "examples": {s: [excerpt(reviews[rid], app_id) for rid in sorted_ids(group[s], reviews)[:4]]
                                    for s in ("P", "N")}})
    # 구간마다 불만으로 가장 많이 나온 주제 둘
    for index, cohort in enumerate(cohorts):
        ranked = sorted(((t["cells"][index]["count"], t["name"]) for t in themes), key=lambda x: (-x[0], x[1]))
        cohort["top_neg"] = [{"name": name, "count": count} for count, name in ranked[:2] if count]
    complaint_ids = set().union(*(g["N"] for g in members.values())) if members else set()
    mood = Counter(item.get("s") if item.get("s") in ("P", "M", "N") else "U"
                   for item in analyzed.values())
    liked = [a for a in analyzed.values() if a.get("s") in ("P", "M")]
    fun_counts = Counter(f for a in liked for f in set(a.get("f") or []) if f in FUN)
    # 유형마다 Steam 도움됨이 가장 많은 리뷰 하나. 같은 글이 두 유형에 나오지 않게 한다.
    fun_example, used = {}, set()
    for f, _ in fun_counts.most_common():
        ids = [rid for rid, a in analyzed.items() if a.get("s") in ("P", "M") and f in (a.get("f") or []) and rid not in used]
        if ids:
            ids = [rid for rid in ids if is_positive(reviews[rid])] or ids   # 게임을 추천한 글을 먼저 고른다
            rid = sorted_ids(ids, reviews)[0]
            used.add(rid)
            fun_example[f] = excerpt(reviews[rid], app_id, 180)
    timestamps = [number(r.get("timestamp_created")) for r in reviews.values()]
    timestamps = [t for t in timestamps if t is not None and t > 0]
    day = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
    # 주제 화면의 숫자가 실제로 나오는 묶음: 주제가 하나라도 붙은 리뷰
    themed = {rid for group in members.values() for key in ("P", "N") for rid in group[key]}
    # Steam 추천 여부 × 글에 담긴 반응. 추천하면서 불만을 쓴 글, 비추천하면서 칭찬한 글이 보인다.
    vote_mood = {"up": dict.fromkeys("PMNU", 0), "down": dict.fromkeys("PMNU", 0)}
    for rid, item in analyzed.items():
        vote_mood["up" if is_positive(reviews[rid]) else "down"][item.get("s") if item.get("s") in "PMN" else "U"] += 1
    # 추천과 비추천은 따로 최신순으로 모은다. 두 묶음의 기간이 어긋났는지 화면이 알려 준다.
    vote_periods = {}
    for key, positive in (("up", True), ("down", False)):
        stamps = [number(r.get("timestamp_created")) for r in reviews.values() if is_positive(r) == positive]
        stamps = [t for t in stamps if t is not None and t > 0]
        if stamps:
            vote_periods[key] = {"start": day(min(stamps)), "end": day(max(stamps)), "days": round((max(stamps) - min(stamps)) / 86400) + 1}
    start_gap = (round(abs(min_stamp(reviews, True) - min_stamp(reviews, False)) / 86400)
                 if len(vote_periods) == 2 else None)
    # 구체적인 불만인데 주제 목록에 없어 "기타"로 빠진 리뷰. 주제 목록과 매트릭스에는 나오지 않으므로 건수라도 보여 준다.
    off_list = sum(any(isinstance(p, (list, tuple)) and len(p) == 2 and p[0] == "기타" and p[1] == "N" for p in item.get("t") or [])
                   for item in analyzed.values())
    return {"counts": {"collected": n, "analyzed": len(analyzed), "negative": n - up, "off_list_complaints": off_list,
                       "themed": len(themed), "vote_mood": vote_mood,
                       "complaint_reviews": len(complaint_ids),
                       "recommended_complaints": sum(is_positive(reviews[rid]) for rid in complaint_ids),
                       "short_excluded": n - len(eligible),
                       "analysis_missing_eligible": len(eligible - analyzed.keys()),
                       "skipped_analysis": skipped},
            "period": {"start": day(min(timestamps)), "end": day(max(timestamps))} if timestamps else None,
            "vote_periods": vote_periods, "vote_period_gap_days": start_gap,
            "promise": promise_check(design, n),
            # 걸러질 때마다 남은 글의 성격이 달라지는지 (치우침 점검)
            "bias": [stage_profile("collected", "수집한 리뷰", list(reviews), reviews),
                     stage_profile("analyzed", "AI가 분석한 글", list(analyzed), reviews),
                     stage_profile("themed", "주제가 붙은 글", sorted(themed), reviews),
                     stage_profile("excluded", "분석에서 뺀 글", [rid for rid in reviews if rid not in analyzed], reviews)],
            "language": (design.get("params") or {}).get("language", "unknown"),
            "languages": Counter(r.get("language") or "unknown" for r in reviews.values()).most_common(8),
            "sample_negative_rate": round((n - up) / n * 100, 1) if n else None,
            "mood": {key: mood[key] for key in ("P", "M", "N", "U")},
            "themes": sorted(themes, key=lambda t: (-t["mentions"], t["name"])),
            "cohorts": cohorts,
            "merges": merges, "n_ai_topics": n_ai_topics,
            "deep": build_deep(reviews, analyzed, members, complaints, app_id, alias),
            "fun_denominator": len(liked),
            "fun": [{"name": f, "desc": FUN[f], "count": c, "share": round(c / len(liked) * 100, 1), "example": fun_example.get(f)}
                    for f, c in fun_counts.most_common()]}


# ── 심층 분석: AI를 다시 부르지 않고 이미 저장된 결과만 다시 센다 ──────────────
EARLY_HOURS = 20
REQUEST_MARKS = ("해주", "해 주", "해라", "했으면", "좋겠", "추가", "수정", "개선", "희망", "부탁", "늘려", "줄여",
                 "바꿔", "복구", "지원", "넣어", "고쳐", "상향", "하향", "가능하게", "필요")
VAGUE = {"버그 수정", "버그 개선", "개선 필요", "최적화 필요", "최적화 개선", "수정 필요", "편의성 개선",
         "버그 개선 희망", "버그 좀 수정해주세요", "고쳐주셨으면 합니다", "개선 부탁"}


_FOREIGN = re.compile(r"[À-ɏЀ-ӿ぀-ヿ㐀-鿿]+")


def korean_only(text):
    """AI가 쓴 문구에 섞여 나온 한자·일본어·키릴·악센트 글자를 뺀다('의미不明', '주의但 재미'). 리뷰 원문에는 쓰지 않는다."""
    return re.sub(r"\s{2,}", " ", _FOREIGN.sub("", str(text or ""))).strip()


GENERIC_WORDS = ("개선", "수정", "필요", "기능", "시스템", "관리", "문제", "해결", "패치", "업데이트", "희망", "요망", "부탁", "제발", "빨리",
                 "해주세요", "해줘", "해라", "좀", "더", "및", "버그", "최적화", "고쳐", "고치", "주세요", "주셨으면", "합니다", "했으면", "좋겠", "잘", "해결", "해주면", "해줬으면", "줬으면", "된다면", "되면",
                 "갓겜", "게임", "완벽", "될 듯", "될듯", "환불", "것 같습니다", "거 같습니다", "plz")


def is_request(text, theme=""):
    """유저가 직접 적은 구체적인 요청인가. 화면의 '바라는 것'과 요약의 제안이 같은 기준을 쓴다.

    주제 이름에 "개선·수정"만 붙인 말("저장 기능 개선", "서버 개선", "패치좀 해라")은 무엇을 바라는지 알 수 없어 뺀다."""
    text = " ".join((text or "").split())
    if not (len(text) >= 6 and text not in VAGUE and any(mark in text for mark in REQUEST_MARKS)):
        return False
    rest = text.replace(theme, "") if theme else text
    for word in GENERIC_WORDS:
        rest = rest.replace(word, "")
    return len(re.sub(r"[^0-9A-Za-z가-힣]", "", rest)) >= 3


def votes(row):
    return int(number(row.get("votes_up")) or 0)


def build_deep(reviews, analyzed, members, complaints, app_id, alias=None):
    parts = {}  # 주제 → [(리뷰 번호, 문제, 원인, 제안)]
    for item in complaints:
        rid = str(item["id"])
        for p in item.get("p") or []:
            name = analysis_design.resolve(p.get("t"), alias or {}) if isinstance(p, dict) and p.get("t") else None
            if name:
                parts.setdefault(name, []).append((rid, korean_only(p.get("prob")), str(p.get("why") or ""), korean_only(p.get("fix"))))
    # ① 주제별 불만 메모: 리뷰마다 AI가 적어 둔 문제·원인 문장. 낱말로 쪼개 세지 않고 문장 그대로, 도움됨이 많은 리뷰부터.
    # (낱말로 세던 때는 형태소 분석 없이 조사를 떼다가 "플레이"가 "플레"로 잘려 나왔다.)
    notes = {}
    for name, rows in parts.items():
        if name == "기타":
            continue
        seen, picked = set(), []
        for rid, prob, why, _ in sorted(rows, key=lambda r: -votes(reviews[r[0]]) if r[0] in reviews else 0):
            key = re.sub(r"\s+", "", prob)
            if not prob or key in seen or rid not in reviews:
                continue
            seen.add(key)
            picked.append({"prob": prob[:60], "why": why[:80], "votes": votes(reviews[rid])})
        notes[name] = picked[:4]

    # ② 초반 이탈: 20시간 전에 비추천한 리뷰와 그 뒤에 비추천한 리뷰가 불만으로 꼽은 주제
    early, later = Counter(), Counter()
    early_n = later_n = 0
    for rid, item in analyzed.items():
        row = reviews[rid]
        hours = review_hours(row)
        if is_positive(row) or hours is None:
            continue
        names = {analysis_design.resolve(n, alias or {}) for n, s in (item.get("t") or []) if s == "N" and n != "기타"}
        if hours < EARLY_HOURS:
            early_n += 1
            early.update(names)
        else:
            later_n += 1
            later.update(names)
    share = lambda c, n: round(c / n * 100, 1) if n else None
    churn_topics = [{"name": k, "early": early[k], "later": later[k],
                     "early_share": share(early[k], early_n), "later_share": share(later[k], later_n)}
                    for k in sorted(set(early) | set(later), key=lambda k: (-early[k], -later[k]))[:6]]

    # ③ 공감 많은 불만: 다른 유저가 "도움됨"을 누른 수
    total_votes = sum(votes(reviews[rid]) for g in members.values() for rid in g["N"]) or 0
    total_neg = sum(len(g["N"]) for g in members.values()) or 0
    # voted = 도움됨을 한 표라도 받은 불만 리뷰 수. 리뷰 하나가 표를 몰아 받았는지 화면이 가려낸다.
    agreed = sorted(({"name": name, "neg": len(g["N"]), "votes": sum(votes(reviews[rid]) for rid in g["N"]),
                      "voted": sum(votes(reviews[rid]) > 0 for rid in g["N"])}
                     for name, g in members.items() if g["N"]), key=lambda t: -t["votes"])[:6]
    for t in agreed:
        t["vote_share"] = share(t["votes"], total_votes)
        t["count_share"] = share(t["neg"], total_neg)
    loudest = sorted({rid for g in members.values() for rid in g["N"]}, key=lambda rid: -votes(reviews[rid]))[:3]
    top_reviews = [dict(excerpt(reviews[rid], app_id, 160),
                        themes=[n for n, g in members.items() if rid in g["N"]]) for rid in loudest if votes(reviews[rid]) > 0]

    # ④ 유저가 원하는 것: 불만 리뷰의 구체적인 요청. 같은 문장은 합치고 공감 순
    wants = {}
    counted_requests = set()
    for name, rows in parts.items():
        for rid, _, _, fix in rows:
            text = " ".join(fix.split())
            if not is_request(text, name):
                continue
            key = re.sub(r"\s+", "", text)
            if (key, rid) in counted_requests:
                continue
            counted_requests.add((key, rid))
            w = wants.setdefault(key, {"text": text[:60], "theme": name, "count": 0, "votes": 0})
            w["count"] += 1
            w["votes"] += votes(reviews[rid])
    wants = sorted(wants.values(), key=lambda w: (-w["count"], -w["votes"]))[:40]   # 화면이 주제별로 골라 쓴다

    return {"early_hours": EARLY_HOURS, "has_complaints": bool(complaints), "notes": notes,
            "churn": {"early_n": early_n, "later_n": later_n, "topics": churn_topics},
            "agreed": {"topics": agreed, "total_votes": total_votes, "reviews": top_reviews},
            "wants": wants}


def evidence_page(directory, app_id, theme, sentiment="N", page=1):
    source = load_sources(directory)
    if source is None:
        return None
    reviews, analyzed = source[0], source[1]
    group = merged_topics(analyzed)[0].get(theme)
    if group is None:
        return None
    ids = group[sentiment] if sentiment in ("P", "N") else group["P"] | group["N"]
    ordered = sorted_ids(ids, reviews)
    return {"theme": theme, "sentiment": sentiment, "total": len(ordered), "page": page,
            "page_size": 20, "reviews": [excerpt(reviews[rid], app_id, limit=20000)
                                         for rid in ordered[(page - 1) * 20:page * 20]]}
