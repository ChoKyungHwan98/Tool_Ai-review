"""현재 인사이트 — 기획자가 바로 쓰는 결론.

추천률은 Steam 원자료를 사용하고, 리뷰에서만 알 수 있는 내용을 집계한다.

  범위     전체 중 몇 건을 모았고 몇 건을 AI가 읽었나
  연관 차이   주제가 붙은 리뷰 중 이 주제를 빼면 추천률이 몇 %p 바뀌나
  재미     추천한 유저가 느낀 재미 종류 (MDA 8가지)
  누가     플레이 시간 구간별 추천률과 주요 불만
  할 일    추천률을 가장 많이 깎는 주제 최대 5개 — 문제 / 원인 / 유저 제안

짧은 리뷰도 추천 여부 집계에는 넣는다. 빼면 칭찬이 빠져서 결과가 실제보다 나빠진다.
"""

import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

import httpx
import analysis_design
import openrouter_limits as limits
from budget_control import current as current_budget
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
from config import cfg
from analyze_reviews_v3 import FUN_TYPES, clean_json, should_classify

SMALL = 30             # 이보다 적은 구간은 "표본 적음"
MIN_THEME = 5          # 이보다 적게 언급된 주제는 영향도 순위에서 뺀다
PLAYTIME_BUCKETS = [(0, 2, "2시간 미만"), (2, 20, "2~20시간"), (20, 100, "20~100시간"), (100, 10 ** 9, "100시간 이상")]


def path(name):
    return os.path.join(os.path.dirname(cfg.ANALYSIS_CSV), name)


def is_up(v):
    return str(v).lower() in ("1", "true")


def read_jsonl(p):
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load():
    with open(cfg.REVIEWS_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reviews = list(csv.DictReader(f))
    analyzed = read_jsonl(path("analysis_v3.jsonl"))
    complaints = read_jsonl(path("complaints_v3.jsonl"))
    with open(path("themes_v3.json"), "r", encoding="utf-8") as f:
        themes = json.load(f)
    design = {}
    if os.path.exists(path("sample_design.json")):
        with open(path("sample_design.json"), "r", encoding="utf-8") as f:
            design = json.load(f)
    return reviews, analyzed, complaints, themes, design


def hours(r):
    value = r.get("playtime_at_review_min")
    try:
        minutes = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        minutes = None
    return minutes / 60 if minutes is not None and minutes >= 0 else None


REQUEST_MARKS = ("해주", "해 주", "해라", "했으면", "좋겠", "추가", "수정", "개선", "희망", "부탁",
                 "늘려", "줄여", "바꿔", "복구", "지원", "넣어", "고쳐", "상향", "하향", "가능하게")
VAGUE = ("버그 개선 희망", "버그 좀 수정해주세요", "고쳐주셨으면 합니다", "편의성 개선", "개선 부탁")


def is_request(text):
    t = text.strip()
    return any(m in t for m in REQUEST_MARKS) and t not in VAGUE and len(t) >= 6


def pick_quote(ids, text_of, votes_of, used=None):
    """공감을 많이 받은, 너무 짧지도 길지도 않은 원문 한 줄. used에 든 리뷰는 건너뛴다."""
    best = None
    for i in sorted(ids):
        if used is not None and i in used:
            continue
        t = " ".join((text_of.get(i) or "").split())
        if not 10 <= len(t) <= 160:
            continue
        key = (votes_of.get(i, 0), -abs(len(t) - 60))
        if best is None or key > best[0]:
            best = (key, t, i)
    if best is None:
        return ""
    if used is not None:
        used.add(best[2])
    t = best[1]
    return t if len(t) <= 90 else t[:88] + "…"


def build():
    reviews, analyzed, complaints, themes, design = load()
    reviews_by_id = {str(r["recommendationid"]): r for r in reviews}
    analyzed = list({str(a["id"]): a for a in analyzed if str(a.get("id")) in reviews_by_id}.values())
    pop_rate = (design.get("population") or {}).get("pos_rate")

    text_of = {r["recommendationid"]: r["content"] for r in reviews}
    votes_of = {r["recommendationid"]: int(r.get("votes_up") or 0) for r in reviews}

    n_collected = len(reviews)
    n_short = sum(not should_classify(r) for r in reviews)
    n_analyzed = len(analyzed)
    analyzed_ids = {str(a["id"]) for a in analyzed}
    n_missing_eligible = sum(should_classify(r) and str(r["recommendationid"]) not in analyzed_ids
                             for r in reviews)
    complaint_ids = {a["id"] for a in analyzed if not a["up"]}
    population = design.get("population") or {}

    # ── 주제별 언급·영향도 ────────────────────────────────────────
    by_theme = defaultdict(lambda: {"ids": set(), "pos": set(), "neg": set()})
    for a in analyzed:
        for name, s in a.get("t") or []:
            if name == "기타" or s not in ("P", "N"):
                continue
            by_theme[name]["ids"].add(a["id"])
            (by_theme[name]["pos"] if s == "P" else by_theme[name]["neg"]).add(a["id"])
    members = {name: {"P": d["pos"], "N": d["neg"]} for name, d in by_theme.items()}
    alias, _ = analysis_design.auto_merge(members)
    merged = analysis_design.merge_members(members, alias)
    by_theme = {name: {"ids": g["P"] | g["N"], "pos": g["P"], "neg": g["N"]}
                for name, g in merged.items()}
    up_of = {rid: is_up(row["voted_up"]) for rid, row in reviews_by_id.items()}
    themed_ids = set().union(*(d["ids"] for d in by_theme.values())) if by_theme else set()
    themed_rate = (sum(up_of[rid] for rid in themed_ids) / len(themed_ids)) if themed_ids else 0

    theme_rows = []
    used_pos, used_neg = set(), set()
    # 많이 언급된 주제가 먼저 좋은 인용문을 가져간다
    theme_meta = {t["name"]: t for t in themes}
    for name in sorted(by_theme, key=lambda n: (-len(by_theme[n]["ids"]), n)):
        t = theme_meta.get(name, {})
        d = by_theme[name]
        # 같은 분류 단계에 들어온 리뷰끼리 비교한다. 전체 수집 리뷰를 섞으면
        # 주제가 붙을 확률 자체가 비추천 여부에 따라 다른 선택 편향이 생긴다.
        other_ids = themed_ids - d["ids"]
        without = sum(up_of[i] for i in other_ids) / len(other_ids) if other_ids else themed_rate
        theme_rows.append({
            "name": name,
            "desc": t.get("desc", ""),
            "mentions": len(d["ids"]),
            "pos": len(d["pos"]),
            "neg": len(d["neg"]),
            "impact": round((themed_rate - without) * 100, 2),
            "small": len(d["ids"]) < MIN_THEME,
            "quote_pos": pick_quote(d["pos"], text_of, votes_of, used_pos),
            "quote_neg": pick_quote(d["neg"], text_of, votes_of, used_neg),
        })
    ranked = [t for t in theme_rows if not t["small"]]
    lifts = sorted([t for t in ranked if t["pos"] > t["neg"] * 2], key=lambda t: -t["pos"])[:3]
    # 불만이 몇 건 없는 주제는 추천률 차이가 음수여도 "할 일"로 올리지 않는다(적을 문제·원인이 없다).
    drags = sorted([t for t in ranked if t["impact"] < 0 and t["neg"] >= MIN_THEME], key=lambda t: t["impact"])[:5]

    # ── 재미 종류 (추천·섞임 리뷰 기준) ────────────────────────────
    liked = [a for a in analyzed if a.get("s") in ("P", "M")]
    fun_count = Counter(x for a in liked for x in set(a.get("f") or []))
    fun = [{"name": k, "desc": FUN_TYPES[k], "share": round(v / len(liked) * 100, 1)}
           for k, v in fun_count.most_common() if k in FUN_TYPES] if liked else []

    # ── 플레이 시간 구간 ──────────────────────────────────────────
    analyzed_by_id = {a["id"]: a for a in analyzed}
    playtime = []
    for lo, hi, label in PLAYTIME_BUCKETS:
        group = [r for r in reviews if (h := hours(r)) is not None and lo <= h < hi]
        if not group:
            continue
        rate = sum(is_up(r["voted_up"]) for r in group) / len(group)
        neg_tags = Counter()
        for r in group:
            a = analyzed_by_id.get(r["recommendationid"])
            if a:
                neg_tags.update({analysis_design.resolve(name, alias) for name, s in a.get("t") or []
                                 if s == "N" and name != "기타"})
        playtime.append({
            "label": label,
            "n": len(group),
            "rate": round(rate * 100, 1),
            "small": len(group) < SMALL,
            "top_neg": [{"name": k, "count": v} for k, v in neg_tags.most_common(2)],
        })

    # ── 할 일 재료: 주제별 불만 원문 조각 ───────────────────────────
    parts_by_theme = defaultdict(lambda: {"prob": [], "why": [], "fix": []})
    for c in complaints:
        for p in c.get("p") or []:
            for k in ("prob", "why", "fix"):
                v = (p.get(k) or "").strip()
                if v and (k != "fix" or is_request(v)):
                    parts_by_theme[analysis_design.resolve(p.get("t"), alias)][k].append(v)

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "coverage": {
            "population": population.get("total"),
            "collected": n_collected,
            "short": n_short,
            "missing_eligible": n_missing_eligible,
            "analyzed": n_analyzed,
            "complaints": len(complaint_ids),
        },
        "rates": {
            "steam": round(pop_rate * 100, 1) if pop_rate else None,
            "collected": round(sum(1 for r in reviews if is_up(r["voted_up"])) / n_collected * 100, 1) if n_collected else None,
        },
        "themes": theme_rows,
        "lifts": lifts,
        "drags": drags,
        "fun": fun[:4],
        "playtime": playtime,
    }
    return result, parts_by_theme


# ── 요약과 할 일: AI 한 번 호출. 실패하면 규칙으로 만든다 ──────────────

SYSTEM_D = "게임 기획자에게 보고하는 리뷰 분석가입니다. 수집 리뷰의 관찰 결과만 설명하고 전체 유저의 비율, 통계적 확실성, 원인·개선 효과로 일반화하지 않습니다. 데이터에 없는 내용은 쓰지 않습니다. JSON 객체만 출력합니다."

USER_D = """게임: {game}

분석 결과:
{facts}

주제별 불만 조각 (prob=문제, why=원인, fix=유저 제안):
{parts}

JSON 객체 하나로 답하세요.
{{"summary":"","actions":[{{"t":"주제","prob":"","why":"","fix":[]}}]}}
- summary: 기획자가 읽을 한 문단, 180자 이내. 잘하는 점과 아쉬운 점을 함께. 숫자는 꼭 필요한 것 1~2개만.
- 모든 비율과 차이는 수집한 리뷰 안의 관찰값입니다. 전체 유저를 대표한다거나 그 주제 때문에 추천률이 바뀐다고 쓰지 마세요.
- actions: 위 불만 주제 순서 그대로. prob는 불만 조각을 종합한 문제 한 줄, 40자 이내.
  "언급 N건" 같은 통계 문장은 쓰지 말고 유저가 겪는 문제를 쓰세요. 조각이 없으면 "".
  why는 문제가 생기는 이유만 쓰고 prob의 말을 되풀이하지 마세요. 조각에 근거가 없으면 "".
  fix는 조각의 fix 중 구체적인 요청만 0~2개 골라 25자 이내로 다듬으세요.
  "버그 고쳐주세요"처럼 막연한 말은 빼고, 조각에 없는 해결책은 절대 만들지 마세요."""


def summary_prompt(game, result, parts_by_theme):
    facts = {
        "칭찬 많은 주제": [f"{t['name']} 칭찬 {t['pos']}건" for t in result["lifts"]],
        "비추천과 연관된 주제": [f"{t['name']} 추천률 차이 {t['impact']}%p, 언급 {t['mentions']}건" for t in result["drags"]],
        "추천 유저가 느낀 재미": [f"{f['name']}({f['desc']}) {f['share']}%" for f in result["fun"][:3]],
        "수집 리뷰의 플레이 시간별 추천률": [f"{p['label']} {p['rate']}%" + (" (표본 적음)" if p["small"] else "") for p in result["playtime"]],
        "스팀 추천률": result["rates"]["steam"],
    }
    # 같은 문장은 한 번만, 종류별로 몇 개만 보낸다. 요약에 필요한 것은 대표 사례이지 전체 목록이 아니다.
    caps = {"prob": 12, "why": 8, "fix": 8}
    parts = {t["name"]: {k: list(dict.fromkeys(v))[:caps.get(k, 8)] for k, v in parts_by_theme.get(t["name"], {}).items()}
             for t in result["drags"]}
    return USER_D.format(
        game=game,
        facts=json.dumps(facts, ensure_ascii=False),
        parts=json.dumps(parts, ensure_ascii=False, separators=(",", ":")),
    )


def ask_summary(game, result, parts_by_theme, prompt=None):
    body = {
        "model": cfg.MODEL,
        "temperature": 0.2,
        "max_tokens": 900,
        "messages": [{"role": "system", "content": SYSTEM_D},
                     {"role": "user", "content": prompt if prompt is not None else summary_prompt(game, result, parts_by_theme)}],
    }
    guard = current_budget()
    reservation = guard.reserve(body["messages"], body["max_tokens"]) if guard else None
    usage_recorded = False
    try:
        limits.wait_turn()
        r = httpx.post(cfg.OPENROUTER_URL, json=body, timeout=120,
                       headers={"Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}", "Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        if guard:
            guard.finish(reservation, usage)
            usage_recorded = True
        text = data["choices"][0]["message"]["content"].strip()
        m = text[text.find("{"): text.rfind("}") + 1]
        return json.loads(m or clean_json(text)), usage
    finally:
        if guard and not usage_recorded:
            guard.finish(reservation)


def rule_summary(result):
    parts = []
    if result["lifts"]:
        parts.append(f"{', '.join(t['name'] for t in result['lifts'][:2])}이(가) 가장 많이 칭찬받습니다.")
    if result["drags"]:
        parts.append(f"{', '.join(t['name'] for t in result['drags'][:2])}은(는) 비추천 리뷰와 연관됩니다.")
    early = next((p for p in result["playtime"] if p["label"] == "2시간 미만"), None)
    if early and not early["small"]:
        parts.append(f"수집한 2시간 미만 리뷰의 추천률은 {early['rate']}%입니다.")
    return " ".join(parts) or "분석할 주제가 충분하지 않습니다."


def record_usage(usage):
    p = path("usage_v3.json")
    data = {}
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    d = data.get("D", {"calls": 0, "input": 0, "output": 0})
    d["calls"] += 1
    d["input"] += int(usage.get("prompt_tokens") or 0)
    d["output"] += int(usage.get("completion_tokens") or 0)
    data["D"] = d
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def main():
    result, parts_by_theme = build()
    game = cfg.get_game_name() if hasattr(cfg, "get_game_name") else str(cfg.APP_ID)

    actions_ai = {}
    prompt = summary_prompt(game, result, parts_by_theme)
    cache_key = hashlib.sha256(json.dumps({"model": cfg.MODEL, "system": SYSTEM_D, "prompt": prompt},
                                          ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    cache_path = path("summary_v5_cache.json")
    try:
        cached = {}
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
        if cached.get("key") == cache_key and isinstance(cached.get("answer"), dict):
            answer = cached["answer"]
            result["summary_cached"] = True
        elif result["lifts"] or result["drags"]:
            answer, usage = ask_summary(game, result, parts_by_theme, prompt)
            record_usage(usage)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"key": cache_key, "answer": answer}, f, ensure_ascii=False)
            result["summary_cached"] = False
        else:
            answer = {"summary": rule_summary(result), "actions": []}
            result["summary_source"] = "rule"
        result["summary"] = str(answer.get("summary", "")).strip() or rule_summary(result)
        result.setdefault("summary_source", "ai")
        for a in answer.get("actions") or []:
            if isinstance(a, dict) and a.get("t"):
                actions_ai[a["t"]] = a
    except Exception as e:
        print(f"  요약 생성 실패, 규칙으로 대체: {str(e)[:120]}")
        result["summary"] = rule_summary(result)
        result["summary_source"] = "rule"

    actions = []
    for rank, t in enumerate(result["drags"], 1):
        a = actions_ai.get(t["name"], {})
        raw = parts_by_theme.get(t["name"], {})
        actions.append({
            "rank": rank,
            "theme": t["name"],
            "impact": t["impact"],
            "mentions": t["mentions"],
            "prob": a.get("prob") or (raw.get("prob") or [""])[0],
            "why": a.get("why") or "",
            "fix": [str(x)[:40] for x in (a.get("fix") or [])][:2],
            "quote": t["quote_neg"],
        })
    result["actions"] = actions

    usage_path = path("usage_v3.json")
    if os.path.exists(usage_path):
        with open(usage_path, "r", encoding="utf-8") as f:
            result["usage"] = json.load(f)

    out = path("insights_v5.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"v5 인사이트 저장: {out}")
    return result


if __name__ == "__main__":
    main()
