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


def interval90(hits, n):
    """비율 hits/n의 90% Wilson 범위 [낮은 %, 높은 %].

    건수가 적어서 생기는 흔들림만 잰다. 무작위로 뽑았고 AI 분류가 맞다는 가정 아래의 범위이므로
    전체 유저에 대한 신뢰구간으로 쓰지 않고, 칭찬·불만 어느 쪽이 많은지 말할 수 있는지만 가린다."""
    if not n:
        return None
    z, p = 1.645, hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** .5) / (1 + z * z / n)
    return [round(max(0.0, centre - half) * 100, 1), round(min(1.0, centre + half) * 100, 1)]


def margin95_exact(n, population):
    """무작위 표본의 최대 비율 오차(%p). 판정에는 반올림 전 값을 쓴다."""
    if not n or not population:
        return None
    if n >= population:
        return 0.0
    return 1.96 * ((0.25 / n) * (population - n) / (population - 1)) ** .5 * 100


def margin95(n, population):
    value = margin95_exact(n, population)
    return round(value, 1) if value is not None else None


def promise_check(design, collected, analyzed, analyzable, missing_eligible=None):
    """표본 오차를 표시할 수 있는지 수집 범위와 분석 누락을 확인한다."""
    params, population = design.get("params") or {}, (design.get("population") or {}).get("total")
    planned, target = (design.get("design") or {}).get("n_total"), params.get("target_error_pct")
    if not planned or target is None:
        return None
    pool = params.get("random_pool") or {}
    random_complete = params.get("sort") == "random" and pool.get("complete") is True
    # 기간을 지정했거나 Steam의 전체 건수가 변했을 수 있으므로 실제로 끝까지 훑은 범위를 분모로 쓴다.
    frame_size = pool.get("size") if random_complete else population
    enough = bool(frame_size) and collected >= min(planned, frame_size)
    actual_margin = margin95_exact(collected, frame_size) if random_complete else None
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
        {"name": "분석 가능한 글을 빠짐없이 분석했는가",
         "ok": bool(analyzable) and analyzed >= analyzable and (missing_eligible is None or missing_eligible == 0),
         "text": f"분석할 수 있는 글 {analyzable:,}건 중 {analyzed:,}건 ({round(analyzed / analyzable * 100) if analyzable else 0}%)"},
    ]
    kept = all(c["ok"] for c in checks)
    return {"target": target, "planned": planned, "population": population, "checks": checks,
            "kept": kept, "frame_size": frame_size if random_complete else None,
            # 추천 여부는 모든 수집 리뷰에 있으므로 실제 수집 건수를 쓴다. AI 주제 비율의 오차가 아니다.
            "analyzed_margin": margin95(collected, frame_size) if kept else None,
            "analyzed": analyzed}


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
    min_len = (design.get("params") or {}).get("min_len") or 2
    eligible_ids = {rid for rid, r in reviews.items() if len((r.get("content") or "").strip()) >= min_len}
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
        neg_range = interval90(len(group["N"]), len(group["P"]) + len(group["N"]))
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
                       "examples": {s: [excerpt(reviews[rid], app_id) for rid in sorted_ids(group[s], reviews)[:2]]
                                    for s in ("P", "N")}})
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
    # 리뷰를 쓴 날짜별 추천·비추천 수. 화면이 일·주·월로 묶어 추이를 그린다.
    by_day = {}
    for row in reviews.values():
        stamp = number(row.get("timestamp_created"))
        if stamp is None or stamp <= 0:
            continue
        slot = by_day.setdefault(day(stamp), [0, 0])
        slot[0 if is_positive(row) else 1] += 1
    analyzed_negative = sum(not is_positive(reviews[rid]) for rid in analyzed)
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
    return {"counts": {"collected": n, "analyzed": len(analyzed), "negative": n - up,
                       "analyzed_negative": analyzed_negative,
                       "themed": len(themed), "vote_mood": vote_mood,
                       "excluded_negative": (n - up) - analyzed_negative,
                       "complaint_reviews": len(complaint_ids),
                       "recommended_complaints": sum(is_positive(reviews[rid]) for rid in complaint_ids),
                       "unknown_playtime": sum(review_hours(r) is None for r in reviews.values()),
            "short_excluded": n - len(eligible_ids),
            "analysis_missing_eligible": len(eligible_ids - analyzed.keys()),
                       "skipped_analysis": skipped},
            "period": {"start": day(min(timestamps)), "end": day(max(timestamps))} if timestamps else None,
            "vote_periods": vote_periods, "vote_period_gap_days": start_gap,
            "promise": promise_check(design, n, len(analyzed), len(eligible_ids), len(eligible_ids - analyzed.keys())),
            # 걸러질 때마다 남은 글의 성격이 달라지는지 (치우침 점검)
            "bias": [stage_profile("collected", "수집한 리뷰", list(reviews), reviews),
                     stage_profile("analyzed", "AI가 분석한 글", list(analyzed), reviews),
                     stage_profile("themed", "주제가 붙은 글", sorted(themed), reviews),
                     stage_profile("excluded", "분석에서 뺀 글", [rid for rid in reviews if rid not in analyzed], reviews)],
            "daily": [{"date": d, "up": by_day[d][0], "down": by_day[d][1]} for d in sorted(by_day)],
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
STOPWORDS = {"게임", "너무", "진짜", "정말", "많이", "조금", "좀", "계속", "자꾸", "때문", "문제", "현상",
             "발생", "있음", "없음", "있다", "없다", "하는", "되는", "되지", "않음", "않는", "안됨", "경우",
             "부분", "관련", "상태", "이후", "이상", "그냥", "매우", "가끔", "자주", "일부", "전체", "유저",
             "플레이", "플레이어", "느낌", "생각", "수준", "정도", "해서", "하고", "으로", "에서"}
SUFFIXES = ("에서는", "으로는", "에서", "으로", "이나", "까지", "부터", "하고", "해서", "하면", "하는", "되는",
            "됨", "함", "음", "이", "가", "은", "는", "을", "를", "에", "의", "도", "로", "과", "와", "만")
REQUEST_MARKS = ("해주", "해 주", "했으면", "좋겠", "추가", "수정", "개선", "희망", "부탁", "늘려", "줄여",
                 "바꿔", "복구", "지원", "넣어", "고쳐", "상향", "하향", "가능하게", "필요")
VAGUE = {"버그 수정", "버그 개선", "개선 필요", "최적화 필요", "최적화 개선", "수정 필요", "편의성 개선"}


# 낱말로 세면 뜻이 없는 것: 꾸미는 말과 이어 주는 말
DROP_WORDS = {"인한", "대한", "위한", "통한", "관한", "따른", "같은", "있음", "없음", "있는", "없는", "겁나", "엄청", "매우"}
DROP_ENDINGS = ("는데", "지만", "어서", "아서", "면서", "니까", "려고", "하다", "한다", "된다", "있다", "없다")


def words(text):
    """짧은 한국어 문장을 뜻 있는 낱말로. 형태소 분석기 없이 흔한 조사·어미만 떼어 낸다."""
    out = set()
    for w in re.findall(r"[0-9A-Za-z가-힣]+", text or ""):
        if w in DROP_WORDS or w.endswith(DROP_ENDINGS):
            continue
        for suffix in SUFFIXES:
            if len(w) > len(suffix) + 1 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        if len(w) >= 2 and w not in STOPWORDS:
            out.add(w)
    return out


def votes(row):
    return int(number(row.get("votes_up")) or 0)


def build_deep(reviews, analyzed, members, complaints, app_id, alias=None):
    parts = {}  # 주제 → [(리뷰 번호, 문제, 원인, 제안)]
    for item in complaints:
        rid = str(item["id"])
        for p in item.get("p") or []:
            name = analysis_design.resolve(p.get("t"), alias or {}) if isinstance(p, dict) and p.get("t") else None
            if name:
                parts.setdefault(name, []).append((rid, str(p.get("prob") or ""), str(p.get("why") or ""), str(p.get("fix") or "")))
    focus = sorted((name for name, g in members.items() if g["N"]),
                   key=lambda name: (-(len(members[name]["N"]) > len(members[name]["P"])), -len(members[name]["N"])))[:4]

    # ① 불만에서 자주 나온 말: 주제별 불만 문장에서 여러 리뷰가 함께 쓴 낱말
    causes = []
    for name in focus:
        rows = parts.get(name) or []
        seen, example = {}, {}
        for rid, prob, why, _ in rows:
            for w in sorted(words(f"{prob} {why}") - words(name)):
                seen.setdefault(w, set()).add(rid)
                example.setdefault(w, prob or why)
        ranked = sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:6]
        terms = [{"word": w, "count": len(ids), "example": example[w][:60]}
                 for w, ids in ranked if len(ids) >= 2]
        causes.append({"theme": name, "reviews": len({r[0] for r in rows}), "terms": terms})

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
            if len(text) < 6 or text in VAGUE or not any(m in text for m in REQUEST_MARKS):
                continue
            key = re.sub(r"\s+", "", text)
            if (key, rid) in counted_requests:
                continue
            counted_requests.add((key, rid))
            w = wants.setdefault(key, {"text": text[:60], "theme": name, "count": 0, "votes": 0})
            w["count"] += 1
            w["votes"] += votes(reviews[rid])
    wants = sorted(wants.values(), key=lambda w: (-w["count"], -w["votes"]))[:8]

    return {"early_hours": EARLY_HOURS, "has_complaints": bool(complaints), "causes": causes,
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
