"""요약과 할 일 — 화면이 집계하지 못하는 두 가지만 만든다.

  요약     기획자가 읽을 한 문단 (AI 한 번 호출, 실패하면 규칙 문장)
  할 일    비추천 쪽으로 기운 주제 최대 5개의 문제 / 원인 / 유저 제안

건수와 비율은 여기서 따로 세지 않는다. dashboard_evidence가 읽고 합친 것과 같은 자료·같은 규칙을 쓴다.
"""

import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

import httpx
import analysis_design
import dashboard_evidence as evidence
import openrouter_limits as limits
from budget_control import current as current_budget

sys.stdout.reconfigure(encoding="utf-8")
from config import cfg

SMALL = 30             # 이보다 적은 구간은 "표본 적음"
MIN_THEME = 5          # 이보다 적게 언급된 주제는 순위에서 뺀다


def path(name):
    return os.path.join(cfg.project_dir(), name)


def load():
    """(리뷰, AI 분류, 불만 메모, 주제 설명, 수집 설계). 화면 집계와 같은 읽기 함수를 쓴다."""
    source = evidence.load_sources(path(""))
    if source is None:
        raise FileNotFoundError("reviews.csv 또는 analysis_v3.jsonl이 없습니다. 수집과 분석을 먼저 실행하세요.")
    reviews, analyzed, design, _, complaints = source
    themes = []
    if os.path.exists(path("themes_v3.json")):
        with open(path("themes_v3.json"), "r", encoding="utf-8") as f:
            themes = json.load(f)
    return reviews, analyzed, complaints, themes, design


def build():
    reviews, analyzed, complaints, themes, design = load()
    members, alias, _, _ = evidence.merged_topics(analyzed)
    up = {rid: evidence.is_positive(row) for rid, row in reviews.items()}

    # ── 주제와 추천 여부의 연관 ────────────────────────────────────
    # 주제가 붙은 리뷰끼리만 견준다. 수집 전체와 견주면, 주제가 붙는 글(길고 불만이 많은 글)과
    # 안 붙는 글("갓겜" 같은 짧은 추천)의 차이가 주제의 차이처럼 보인다.
    themed = set().union(*(g["P"] | g["N"] for g in members.values())) if members else set()
    themed_rate = sum(up[rid] for rid in themed) / len(themed) if themed else 0
    desc = {t.get("name"): t.get("desc", "") for t in themes}
    rows = []
    for name, group in members.items():
        ids = group["P"] | group["N"]
        others = themed - ids
        without = sum(up[rid] for rid in others) / len(others) if others else themed_rate
        rows.append({"name": name, "desc": desc.get(name, ""), "mentions": len(ids),
                     "pos": len(group["P"]), "neg": len(group["N"]),
                     "impact": round((themed_rate - without) * 100, 2)})
    rows.sort(key=lambda t: (-t["mentions"], t["name"]))
    ranked = [t for t in rows if t["mentions"] >= MIN_THEME]
    lifts = sorted([t for t in ranked if t["pos"] > t["neg"] * 2], key=lambda t: -t["pos"])[:3]
    # 불만이 몇 건 없는 주제는 추천률 차이가 음수여도 "할 일"로 올리지 않는다(적을 문제·원인이 없다).
    drags = sorted([t for t in ranked if t["impact"] < 0 and t["neg"] >= MIN_THEME], key=lambda t: t["impact"])[:5]

    # ── AI 요약에 건넬 사실: 재미 종류, 플레이 시간별 추천률 ──────────
    liked = [a for a in analyzed.values() if a.get("s") in ("P", "M")]
    fun_count = Counter(f for a in liked for f in set(a.get("f") or []) if f in evidence.FUN)
    fun = [{"name": k, "desc": evidence.FUN[k], "share": round(v / len(liked) * 100, 1)}
           for k, v in fun_count.most_common(4)]
    playtime = []
    for index, (_, _, label) in enumerate(evidence.BUCKETS):
        group = [rid for rid, row in reviews.items() if evidence.bucket_index(row) == index]
        if group:
            playtime.append({"label": label, "n": len(group), "small": len(group) < SMALL,
                             "rate": round(sum(up[rid] for rid in group) / len(group) * 100, 1)})

    # ── 할 일 재료: 주제별 불만 메모 ────────────────────────────────
    parts_by_theme = defaultdict(lambda: {"prob": [], "why": [], "fix": []})
    for c in complaints:
        for p in c.get("p") or []:
            if not isinstance(p, dict) or not p.get("t"):
                continue
            for k in ("prob", "why", "fix"):
                v = str(p.get(k) or "").strip()
                if v and (k != "fix" or evidence.is_request(v)):
                    parts_by_theme[analysis_design.resolve(p["t"], alias)][k].append(v)

    pop_rate = (design.get("population") or {}).get("pos_rate")
    return {"themes": rows, "lifts": lifts, "drags": drags, "fun": fun, "playtime": playtime,
            "rates": {"steam": round(pop_rate * 100, 1) if pop_rate else None}}, parts_by_theme

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
        **limits.model_fields(),
        "temperature": 0.2,
        "max_tokens": limits.answer_room(900),
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
        return json.loads(m), usage
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
    for t in result["drags"]:
        a = actions_ai.get(t["name"], {})
        raw = parts_by_theme.get(t["name"], {})
        actions.append({"theme": t["name"],
                        "prob": a.get("prob") or (raw.get("prob") or [""])[0],
                        "why": a.get("why") or "",
                        "fix": [str(x)[:40] for x in (a.get("fix") or [])][:2]})

    # 저장하는 것은 화면이 읽는 것뿐이다. 건수와 비율은 화면을 열 때 dashboard_evidence가 다시 센다.
    saved = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
             "summary": result["summary"], "summary_source": result["summary_source"],
             "summary_cached": result.get("summary_cached", False),
             "themes": [{"name": t["name"], "desc": t["desc"]} for t in result.get("themes", [])],
             "actions": actions}
    usage_path = path("usage_v3.json")
    if os.path.exists(usage_path):
        with open(usage_path, "r", encoding="utf-8") as f:
            saved["usage"] = json.load(f)

    out = path("insights_v5.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=2)
    print(f"v5 인사이트 저장: {out}")
    return saved

if __name__ == "__main__":
    main()
