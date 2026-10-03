"""분석 설계: 주제 자동 합치기와 분석 설계서. AI를 부르지 않는다.

가공 방법은 기획자가 설계한 규칙이고, 실행은 자동이다.
실행할 때 사람이 고르는 것은 무엇을(게임·언어·기간·정렬)과 얼마나(수집량)뿐이다.
"""
import re

LANGS = {"koreana": "한국어", "english": "영어", "japanese": "일본어", "schinese": "중국어 간체",
         "tchinese": "중국어 번체", "all": "모든 언어"}
MERGE_OVERLAP = 0.8   # 작은 주제의 (리뷰, 칭찬/불만) 중 이만큼이 큰 주제와 겹치면 같은 주제로 본다
MERGE_MIN = 3         # 이보다 적게 언급된 주제는 겹침 비율을 믿기 어려워 합치지 않는다


def resolve(name, alias):
    """합치기를 따라가 최종 이름을 돌려준다."""
    seen = set()
    while name in alias and name not in seen:
        seen.add(name)
        name = alias[name]
    return name


def normalized(name):
    return re.sub(r"[\s·/_\-.,]+", "", str(name)).lower()


def auto_merge(members):
    """같은 주제를 AI가 다른 이름으로 나눈 경우를 규칙으로 합친다.

    members: {주제: {"P": {리뷰}, "N": {리뷰}}}
    규칙 1. 띄어쓰기·기호만 다른 이름은 같은 주제.
    규칙 2. 작은 주제의 (리뷰, 칭찬/불만) 쌍 중 80% 이상이 큰 주제에도 있으면 같은 주제.
    돌려주는 값: alias {작은 주제: 큰 주제}, notes [{from, to, overlap, reason}]
    """
    groups = {name: {(rid, s) for s in ("P", "N") for rid in g[s]} for name, g in members.items()}
    alias, notes = {}, []
    order = lambda name: (-len(groups[name]), name)   # 큰 주제가 이름을 남긴다
    while True:
        best = None
        names = sorted(groups, key=order)
        for i, big in enumerate(names):
            for small in names[i + 1:]:
                if normalized(small) == normalized(big):
                    candidate = (2.0, big, small, "같은 이름")
                elif len(groups[small]) >= MERGE_MIN:
                    ratio = len(groups[small] & groups[big]) / len(groups[small])
                    if ratio < MERGE_OVERLAP:
                        continue
                    candidate = (ratio, big, small, "같은 리뷰에 함께 붙음")
                else:
                    continue
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is None:
            return alias, notes
        ratio, big, small, reason = best
        groups[big] |= groups.pop(small)
        alias[small] = big
        for source, target in alias.items():
            if target == small:
                alias[source] = big
        notes.append({"from": small, "to": big, "reason": reason,
                      "overlap": None if ratio > 1 else round(ratio * 100)})


def merge_members(members, alias):
    merged = {}
    for name, group in members.items():
        target = merged.setdefault(resolve(name, alias), {"P": set(), "N": set()})
        target["P"] |= group["P"]
        target["N"] |= group["N"]
    return merged


def conclusion(themes):
    """칭찬 최다 · 불만 최다 주제. 화면(review-dashboard.js groups)과 같은 규칙. 무엇을 고칠지는 사람이 판단한다."""
    praised = sorted((t for t in themes if t["pos"] >= t["neg"] and t["pos"] > 0),
                     key=lambda t: (-t["pos"], t["neg"], t["name"]))
    disliked = sorted((t for t in themes if t["neg"] > t["pos"]), key=lambda t: (-t["neg"], t["pos"], t["name"]))
    return (praised[0] if praised else None), (disliked[0] if disliked else None)


def design_log(sample_design, counts, usage, n_ai_topics, merges, themes, game=""):
    """분석 설계서. who = 사람(실행할 때 고름) | AI(제안·분류) | 규칙(기획자가 설계, 자동 실행)."""
    params = (sample_design or {}).get("params") or {}
    design = (sample_design or {}).get("design") or {}
    counts = counts or {}
    since = params.get("since")
    rows = [{
        "step": "무엇을", "who": "사람",
        "text": " · ".join(x for x in [game, LANGS.get(params.get("language"), params.get("language") or ""),
                                         f"{since.replace('-', '.')} 이후" if since else "전체 기간",
                                         {"helpful": "공감순", "random": "무작위"}.get(params.get("sort"), "최신순")] if x),
    }]
    planned = design.get("n_total")
    target = params.get("target_error_pct")
    how_much = []
    if planned:
        how_much.append(f"{planned:,}건 계획" + (f" (표본 크기를 정한 기준 ±{target}%)" if target else ""))
    if counts.get("collected") is not None:
        how_much.append(f"{counts['collected']:,}건 수집")
    rows.append({"step": "얼마나", "who": "사람", "text": " → ".join(how_much) or "기록 없음",
                 "details": ["±값은 몇 건을 모을지 정할 때 쓴 기준이다. 최신순으로 모은 리뷰라 결과의 정확도나 신뢰구간을 뜻하지 않는다"] if planned and target else []})
    if (usage or {}).get("model"):
        rows.append({"step": "분석 모델", "who": "사람", "text": usage["model"]})
    if counts.get("collected") is not None and counts.get("analyzed") is not None:
        short = counts.get("short_excluded", counts["collected"] - counts["analyzed"])
        missing = counts.get("analysis_missing_eligible", 0)
        rows.append({"step": "분석 대상", "who": "규칙",
                     "text": f"너무 짧은 리뷰 {short:,}건 제외 → AI 분석 {counts['analyzed']:,}건"
                             + (f" · 분석 대상 {missing:,}건 누락" if missing else "")})
    rows.append({"step": "주제 제안", "who": "AI",
                 "text": f"리뷰를 읽고 주제 {n_ai_topics}개를 제안, 리뷰마다 주제와 칭찬·불만을 붙임"})
    rows.append({"step": "주제 합치기", "who": "규칙",
                 "text": (f"같은 리뷰에 {round(MERGE_OVERLAP * 100)}% 이상 함께 붙은 주제는 하나로 · "
                          + (f"{len(merges)}개를 합쳐 주제 {len(themes)}개" if merges else f"합칠 주제 없음 · 주제 {len(themes)}개")),
                 "details": [f"{m['from']} → {m['to']}" + (f" (겹침 {m['overlap']}%)" if m.get("overlap") is not None else " (같은 이름)")
                             for m in merges]})
    rows.append({"step": "판단 기준", "who": "규칙",
                 "text": "불만이 칭찬보다 많으면 불만 우세 · 지도 기준선은 언급 수 중앙값과 불만 50%"})
    keep, fix = conclusion(themes)
    parts = []
    if keep:
        parts.append(f"칭찬 최다 = {keep['name']} (칭찬 {keep['pos']:,}건)")
    if fix:
        parts.append(f"불만 최다 = {fix['name']} (불만 {fix['neg']:,}건)")
    rows.append({"step": "강조한 주제", "who": "규칙",
                 "text": " · ".join(parts) or "칭찬·불만이 붙은 주제가 없습니다",
                 "details": ["칭찬이 더 많은 주제 중 칭찬이 가장 많은 것과 불만이 더 많은 주제 중 불만이 가장 많은 것을 매트릭스에서 진하게 칠한다. 무엇을 고칠지는 사람이 판단한다"]})
    return rows
