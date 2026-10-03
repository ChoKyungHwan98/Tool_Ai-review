"""AI 분류와 Steam 추천 여부의 일치 점검.

목표:
1. 487건 분석 결과에서 5카테고리 × 6건 = 30건 검증 세트 자동 선정
2. 30건을 LLM에 재호출 (confidence 필드 포함 프롬프트)
3. voted_up과 AI 감성 분류의 일치율 측정 (정답률 아님)
4. AI가 보고한 확신도 분포와 추천 여부가 반대인 사례 식별
5. verify_report.json 생성 → 대시보드 "신뢰도 검증" 탭에서 시각화

5카테고리 (강의 표준):
- 명확한 긍정 (clear_positive)     : voted_up=1 + 분석 sentiment=POSITIVE
- 명확한 부정 (clear_negative)     : voted_up=0 + 분석 sentiment=NEGATIVE
- 모호함 (ambiguous)               : 분석 sentiment=MIXED
- 라벨 불일치 (label_mismatch)     : voted_up과 sentiment 충돌 (어려운 케이스)
- 중립 (neutral)                   : 분석 sentiment=NEUTRAL

실행:
  python verify_analysis.py --select   # 30건 선정 + LLM 재분석 ($0.005)
  python verify_analysis.py --report   # 추천 여부 일치율/확신도 점검 + JSON 저장
  python verify_analysis.py            # 위 두 단계 모두 실행
"""

import os
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import csv
import json
import time
import re
import random
import argparse
import httpx
import openrouter_limits as limits
from budget_control import BudgetExceeded, current as current_budget
from collections import defaultdict

from config import cfg

API_KEY = cfg.OPENROUTER_API_KEY
URL = cfg.OPENROUTER_URL

def get_analysis_csv(): return cfg.ANALYSIS_CSV
def get_verify_set_csv(): 
    return cfg.project_file("verify_set.csv")
def get_verify_report_json(): return cfg.VERIFY_JSON

PER_CATEGORY = 6
TOTAL_SAMPLES = PER_CATEGORY * 5  # 30건

# 신뢰도 임계값 (강의 표준)
HIGH_CONF = 0.90
MID_CONF  = 0.75


# ============ 1. 카테고리 분류 ============
def categorize(row: dict) -> str:
    """기존 분석 결과 + voted_up으로 5카테고리 자동 분류."""
    sent = row.get("overall_sentiment", "").upper()
    try:
        vu = int(row.get("voted_up", 0))
    except (ValueError, TypeError):
        vu = 0

    if sent == "NEUTRAL":
        return "neutral"
    if sent == "MIXED":
        return "ambiguous"
    if vu == 1 and sent == "POSITIVE":
        return "clear_positive"
    if vu == 0 and sent == "NEGATIVE":
        return "clear_negative"
    # voted_up과 sentiment 충돌 = 어려운 케이스
    if (vu == 1 and sent == "NEGATIVE") or (vu == 0 and sent == "POSITIVE"):
        return "label_mismatch"
    return "neutral"


def select_samples(rows: list) -> list:
    """5카테고리 × 6건 균등 선정 (랜덤 시드 고정으로 재현 가능)."""
    random.seed(42)
    buckets = defaultdict(list)
    for r in rows:
        buckets[categorize(r)].append(r)

    selected = []
    for cat in ["clear_positive", "clear_negative", "ambiguous", "label_mismatch", "neutral"]:
        pool = buckets.get(cat, [])
        if not pool:
            print(f"  [WARN] 카테고리 '{cat}' 데이터 없음 — skip")
            continue
        # 너무 짧지 않은 리뷰만 (정확도 검증에 의미 있게)
        valid = [r for r in pool if len(r.get("content", "")) >= 30]
        if len(valid) < PER_CATEGORY:
            valid = pool  # fallback
        sampled = random.sample(valid, min(PER_CATEGORY, len(valid)))
        for r in sampled:
            r["_category"] = cat
            selected.append(r)
        print(f"  [OK] {cat}: {len(sampled)}건 선정 (pool={len(pool)})")
    return selected


# ============ 2. LLM 재분석 (confidence 포함) ============
VERIFY_SYSTEM = (
    "당신은 게임 유저 데이터 분석 전문가입니다. "
    "Steam 유저 리뷰를 언어에 관계없이 분석하여 감성과 함께 분석 신뢰도(confidence)를 정직하게 보고합니다. "
    "확신할 수 없을 때 confidence를 낮추는 것이 매우 중요합니다."
)

VERIFY_USER = """다음 스팀 유저 리뷰의 감성을 분석하세요. 리뷰의 언어와 관계없이 사유는 한국어로 답하세요.

리뷰: "{content}"

아래 JSON 한 덩어리만 출력해주세요 (코드블록 금지).

{{
  "sentiment": "POSITIVE | NEGATIVE | MIXED | NEUTRAL",
  "confidence": 0.0 ~ 1.0 사이 숫자 (이 분석에 대한 확신도),
  "reasoning": "왜 이 감성으로 판단했는지 30자 이내 사유"
}}

confidence 가이드:
- 0.90 이상: 매우 명확한 긍정/부정 (반어법·맥락 모호함 없음)
- 0.75~0.89: 다소 명확하지만 일부 모호한 표현 있음
- 0.60~0.74: 혼합된 신호, 한쪽으로 기울어 보임
- 0.60 미만: 매우 모호하거나 신호 불명확

정직하게 보고하세요. 확신 못하면 낮은 confidence를 사용하세요."""


def clean_json(text: str) -> str:
    text = text.strip()
    m = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if m: return m.group(1).strip()
    m = re.search(r"```\s*([\s\S]*?)\s*```", text)
    if m: return m.group(1).strip()
    m = re.search(r"(\{[\s\S]*\})", text)
    if m: return m.group(1).strip()
    return text


def call_llm(content: str, retries: int = 2) -> dict:
    user = VERIFY_USER.format(content=content[:1500])
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": cfg.MODEL,
        "messages": [
            {"role": "system", "content": VERIFY_SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "max_tokens": 200,
    }
    last_err = None
    for attempt in range(retries + 1):
        guard = current_budget()
        reservation = guard.reserve(body["messages"], body["max_tokens"]) if guard else None
        usage_recorded = False
        try:
            limits.wait_turn()
            r = httpx.post(URL, headers=headers, json=body, timeout=60.0)
            if r.status_code == 429:
                if limits.daily_limit_hit(r.text):
                    raise BudgetExceeded(limits.DAILY_MESSAGE)
                time.sleep(limits.retry_after(r))
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage") or {}
            if guard:
                guard.finish(reservation, usage)
                usage_recorded = True
            usage_path = cfg.project_file("usage_v3.json")
            try:
                with open(usage_path, "r", encoding="utf-8") as stream:
                    usage_data = json.load(stream)
            except (FileNotFoundError, json.JSONDecodeError):
                usage_data = {}
            previous = usage_data.get("V") or {}
            usage_data["V"] = {
                "calls": int(previous.get("calls") or 0) + 1,
                "input": int(previous.get("input") or 0) + int(usage.get("prompt_tokens") or 0),
                "output": int(previous.get("output") or 0) + int(usage.get("completion_tokens") or 0),
            }
            usage_data["model"] = cfg.MODEL
            with open(usage_path, "w", encoding="utf-8") as stream:
                json.dump(usage_data, stream, ensure_ascii=False, indent=2)
            txt = data["choices"][0]["message"]["content"]
            parsed = json.loads(clean_json(txt))
            if not isinstance(parsed, dict) or parsed.get("sentiment") not in (
                    "POSITIVE", "NEGATIVE", "MIXED", "NEUTRAL"):
                raise ValueError("검증 응답의 반응 분류 형식이 올바르지 않습니다")
            confidence = float(parsed.get("confidence"))
            if not 0 <= confidence <= 1:
                raise ValueError("검증 응답의 확신도 범위가 올바르지 않습니다")
            parsed["confidence"] = confidence
            return parsed
        except BudgetExceeded:
            raise
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))  # exponential backoff
        finally:
            if guard and not usage_recorded:
                guard.finish(reservation)
    raise last_err


def run_selection_and_reanalysis():
    """30건 선정 + LLM confidence 재분석 → verify_set.csv."""
    if not API_KEY or "여기에" in API_KEY:
        raise SystemExit("[FAIL] .env의 OPENROUTER_API_KEY를 확인하세요.")
    if not os.path.exists(get_analysis_csv()):
        raise SystemExit(f"[FAIL] {get_analysis_csv()} 없음 — analyze_reviews_v3.py 먼저 실행")

    print("=" * 60)
    print(f"30건 검증 세트 선정 + LLM 재분석 (confidence 포함)")
    print("=" * 60)

    rows = []
    with open(get_analysis_csv(), "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    # 분석 CSV의 content는 500자로 잘려 있다. 재검증에는 저장된 원문 전체를 쓴다.
    with open(cfg.REVIEWS_CSV, "r", encoding="utf-8-sig", newline="") as f:
        original_content = {r["recommendationid"]: r.get("content", "") for r in csv.DictReader(f)}
    rows = [r for r in rows if r.get("recommendationid") in original_content]
    for r in rows:
        r["content"] = original_content[r["recommendationid"]]
    print(f"\n[로드] {len(rows)}건 분석 결과")

    print("\n[카테고리 분류 + 30건 균등 선정]")
    samples = select_samples(rows)
    print(f"\n총 {len(samples)}건 선정 완료\n")

    print("[LLM 재분석 시작 — confidence 필드 포함]")
    results = []
    n_ok = n_fail = 0
    for i, r in enumerate(samples, 1):
        rid = r["recommendationid"]
        content = r["content"]
        try:
            res = call_llm(content)
            results.append({
                "recommendationid": rid,
                "category": r["_category"],
                "content": content[:200],
                "voted_up": r["voted_up"],
                "original_sentiment": r["overall_sentiment"],
                "predicted_sentiment": res.get("sentiment", ""),
                "confidence": float(res.get("confidence", 0.0)),
                "reasoning": (res.get("reasoning", "") or "")[:120],
            })
            n_ok += 1
            print(f"  [{i:2}/{len(samples)}] OK  cat={r['_category']:16s} "
                  f"pred={res.get('sentiment','?'):8s} conf={res.get('confidence',0):.2f}")
        except BudgetExceeded:
            raise
        except Exception as e:
            n_fail += 1
            print(f"  [{i:2}/{len(samples)}] FAIL rid={rid}: {str(e)[:80]}")

    print(f"\n[완료] {n_ok}건 분석 / {n_fail}건 실패")

    fields = ["recommendationid", "category", "content", "voted_up",
              "original_sentiment", "predicted_sentiment", "confidence", "reasoning"]
    with open(get_verify_set_csv(), "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    print(f"[저장] {get_verify_set_csv()}")
    return results


# ============ 3. 정확도/신뢰도 측정 ============
def voted_up_to_expected(vu: int) -> str:
    """Steam 추천 여부를 비교용 라벨로 매핑한다. 글의 감성 정답은 아니다."""
    return "POSITIVE" if vu == 1 else "NEGATIVE"


def classify_outcome(predicted: str, voted_up: int) -> str:
    """예측 결과를 3분류:
       - correct: 예측이 voted_up과 직접 일치 (POSITIVE↔1, NEGATIVE↔0)
       - cautious: AI가 MIXED/NEUTRAL로 보수적 판정 (틀렸다고 단정 못함)
       - wrong: 예측이 voted_up과 명확히 반대
    """
    if predicted == "POSITIVE": return "correct" if voted_up == 1 else "wrong"
    if predicted == "NEGATIVE": return "correct" if voted_up == 0 else "wrong"
    if predicted in ("MIXED", "NEUTRAL"): return "cautious"
    return "wrong"


def is_correct(predicted: str, voted_up: int) -> bool:
    """strict 기준 (lenient는 별도 계산)."""
    return classify_outcome(predicted, voted_up) == "correct"


def confidence_bucket(c: float) -> str:
    if c >= HIGH_CONF: return "high"
    if c >= MID_CONF:  return "mid"
    return "low"


def build_report(results: list) -> dict:
    """4사분면 매트릭스 + 신뢰도 분포 + 카테고리별 정확도 + 위험 케이스."""
    n = len(results)
    if n == 0:
        return {"error": "no data"}

    # CSV에서 읽은 경우 모든 값이 string이므로 타입 변환
    for r in results:
        r["confidence"] = float(r.get("confidence", 0) or 0)
        r["voted_up"]   = int(r.get("voted_up", 0) or 0)
        r["outcome"]    = classify_outcome(r["predicted_sentiment"], r["voted_up"])
        r["correct"]    = (r["outcome"] == "correct")

    n_correct  = sum(1 for r in results if r["outcome"] == "correct")
    n_cautious = sum(1 for r in results if r["outcome"] == "cautious")
    n_wrong    = sum(1 for r in results if r["outcome"] == "wrong")
    accuracy_strict  = n_correct / n * 100
    accuracy_lenient = (n_correct + n_cautious) / n * 100  # 일치 또는 혼합/중립

    confidences = [r["confidence"] for r in results]
    avg_conf = sum(confidences) / n * 100

    # 4사분면 매트릭스 (신뢰도 vs 정답)
    # 평가 기준: outcome=="wrong"만 실제 오답으로 처리, "cautious"는 위험 카운트에서 제외
    matrix = {
        "high_conf_correct":   {"count": 0, "label": "높은 확신 + 추천 여부 일치", "interpretation": "원문 확인 가능", "tone": "best"},
        "high_conf_incorrect": {"count": 0, "label": "높은 확신 + 추천 여부 반대", "interpretation": "원문 확인 우선", "tone": "danger"},
        "low_conf_correct":    {"count": 0, "label": "낮은 확신 + 추천 여부 일치", "interpretation": "원문 확인 권장", "tone": "warn"},
        "low_conf_incorrect":  {"count": 0, "label": "낮은 확신 + 추천 여부 반대", "interpretation": "원문 확인 권장", "tone": "info"},
    }
    for r in results:
        is_high = r["confidence"] >= MID_CONF  # 0.75 기준 (강의)
        outcome = r["outcome"]
        if outcome == "cautious":
            # 보수적 판정은 4사분면에 포함하지 않음 (별도 카테고리)
            continue
        if is_high and outcome == "correct":   matrix["high_conf_correct"]["count"] += 1
        elif is_high and outcome == "wrong":   matrix["high_conf_incorrect"]["count"] += 1
        elif not is_high and outcome == "correct": matrix["low_conf_correct"]["count"] += 1
        else: matrix["low_conf_incorrect"]["count"] += 1

    # 신뢰도 히스토그램 (10개 빈)
    bins = [0]*10
    for c in confidences:
        idx = min(int(c * 10), 9)
        bins[idx] += 1
    histogram = [
        {"range": f"{i*10}~{(i+1)*10}%", "count": bins[i],
         "level": "high" if i >= 9 else ("mid" if i >= 7 else "low")}
        for i in range(10)
    ]

    # 카테고리별 정확도
    cat_stats = defaultdict(lambda: {"n": 0, "correct": 0, "conf_sum": 0.0})
    for r in results:
        s = cat_stats[r["category"]]
        s["n"] += 1
        s["correct"] += 1 if r["correct"] else 0
        s["conf_sum"] += r["confidence"]
    category_accuracy = []
    cat_kr = {"clear_positive":"명확한 긍정", "clear_negative":"명확한 부정",
              "ambiguous":"모호함 (MIXED)", "label_mismatch":"라벨 불일치",
              "neutral":"중립"}
    for cat, s in cat_stats.items():
        category_accuracy.append({
            "category": cat,
            "category_kr": cat_kr.get(cat, cat),
            "n": s["n"],
            "correct": s["correct"],
            "accuracy_pct": round(s["correct"] / s["n"] * 100, 1) if s["n"] else 0,
            "avg_confidence_pct": round(s["conf_sum"] / s["n"] * 100, 1) if s["n"] else 0,
        })
    category_accuracy.sort(key=lambda x: x["accuracy_pct"])

    # 위험 케이스 (고신뢰 + 명확한 오답) — outcome=="wrong"만, cautious 제외
    danger_cases = [
        {
            "recommendationid": r["recommendationid"],
            "category": cat_kr.get(r["category"], r["category"]),
            "content": r["content"][:140],
            "voted_up": int(r["voted_up"]),
            "predicted": r["predicted_sentiment"],
            "confidence": round(r["confidence"] * 100, 1),
            "reasoning": r["reasoning"],
        }
        for r in results
        if r["confidence"] >= MID_CONF and r["outcome"] == "wrong"
    ]
    danger_cases.sort(key=lambda x: -x["confidence"])

    # 신뢰도 구간별 정확도 (cautious 제외, wrong 기준)
    conf_band_accuracy = []
    for label, lo, hi in [("low (<75%)", 0, 0.75), ("mid (75~89%)", 0.75, 0.90), ("high (≥90%)", 0.90, 1.01)]:
        bucket = [r for r in results if lo <= r["confidence"] < hi]
        if not bucket: continue
        # cautious 제외하고 명확한 예측만으로 계산
        decisive = [r for r in bucket if r["outcome"] != "cautious"]
        if not decisive:
            acc = None
        else:
            acc = sum(1 for r in decisive if r["outcome"] == "correct") / len(decisive) * 100
        conf_band_accuracy.append({
            "band": label, "n": len(bucket), "n_decisive": len(decisive),
            "accuracy_pct": round(acc, 1) if acc is not None else None,
            "avg_confidence_pct": round(sum(r["confidence"] for r in bucket) / len(bucket) * 100, 1),
        })

    return {
        "n_total": n,
        "n_correct": n_correct,
        "n_cautious": n_cautious,
        "n_wrong": n_wrong,
        "accuracy_pct": round(accuracy_strict, 1),  # 이전 파일과 호환되는 키. 의미는 추천 여부 일치율.
        "accuracy_lenient_pct": round(accuracy_lenient, 1),
        "recommendation_agreement_pct": round(accuracy_strict, 1),
        "agreement_or_mixed_pct": round(accuracy_lenient, 1),
        "metric_note": "Steam 추천 여부와 AI 감성 분류의 일치율입니다. 사람의 정답 판정이나 전체 AI 정확도가 아닙니다. 범주별로 의도적으로 고른 점검용 표본입니다.",
        "avg_confidence_pct": round(avg_conf, 1),
        "danger_count": len(danger_cases),
        "thresholds": {"high": HIGH_CONF * 100, "mid": MID_CONF * 100},
        "matrix": matrix,
        "histogram": histogram,
        "category_accuracy": category_accuracy,
        "conf_band_accuracy": conf_band_accuracy,
        "danger_cases": danger_cases,
        "samples": results,
        "insight": {
            "title": "추천 여부와 AI 분류의 일치 점검",
            "gap_pct": None,
            "interpretation": (
                f"점검한 {n}건 중 추천 여부와 AI 감성이 직접 일치한 비율은 {accuracy_strict:.1f}%입니다. "
                f"MIXED/NEUTRAL {n_cautious}건은 일치·반대 어느 쪽으로도 판단하지 않았습니다. "
                f"AI의 평균 확신도 {avg_conf:.1f}%는 정답 확률로 검증된 값이 아닙니다."
            ),
            "recommendations": [
                f"확신도가 높지만 추천 여부와 반대인 사례 {len(danger_cases)}건은 원문 확인",
                "실제 AI 정확도를 알려면 사람이 원문에 정답 라벨을 붙인 별도 검증 세트가 필요합니다",
            ],
        },
    }


def run_report():
    if not os.path.exists(get_verify_set_csv()):
        raise SystemExit(f"[FAIL] {get_verify_set_csv()} 없음 — --select 먼저 실행")
    with open(get_verify_set_csv(), "r", encoding="utf-8-sig", newline="") as f:
        results = list(csv.DictReader(f))
    report = build_report(results)
    with open(get_verify_report_json(), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"[신뢰도 검증 리포트]")
    print("=" * 60)
    print(f"  추천 여부와 직접 일치: {report['recommendation_agreement_pct']:.1f}% ({report['n_correct']}/{report['n_total']})")
    print(f"  일치 또는 혼합/중립:  {report['agreement_or_mixed_pct']:.1f}%")
    print(f"  AI가 보고한 평균 확신도: {report['avg_confidence_pct']:.1f}% (정답 확률 아님)")
    print(f"  높은 확신 + 추천 여부 반대: {report['danger_count']}건")
    print(f"  보수적 판정(MIXED/NEUTRAL): {report['n_cautious']}건")
    print()
    print(f"[4사분면 매트릭스] (cautious 제외, decisive {report['n_correct']+report['n_wrong']}건 기준)")
    for k, v in report["matrix"].items():
        print(f"  {v['label']:<18s}: {v['count']}건 — {v['interpretation']}")
    print()
    print(f"[카테고리별 추천 여부 일치율]")
    for c in report["category_accuracy"]:
        print(f"  {c['category_kr']:<16s}: {c['accuracy_pct']:5.1f}%  "
              f"(평균 신뢰도 {c['avg_confidence_pct']:.1f}%, n={c['n']})")
    print()
    print(f"[저장] {get_verify_report_json()}")


# ============ entry ============
def main(args_list=None):
    if args_list is None:
        import sys
        # 만약 uvicorn이나 main.py 등 서버 프로세스 내에서 임포트되어 호출된 경우, sys.argv 파싱을 피합니다.
        if sys.argv and any(x in sys.argv[0] or x in sys.argv for x in ['uvicorn', 'main.py', '-m', 'main']):
            args_list = []
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--select", action="store_true", help="30건 선정 + LLM 재분석")
    parser.add_argument("--report", action="store_true", help="리포트 생성")
    args = parser.parse_args(args_list)

    if not args.select and not args.report:
        # 둘 다 실행
        run_selection_and_reanalysis()
        run_report()
        return
    if args.select:
        run_selection_and_reanalysis()
    if args.report:
        run_report()


if __name__ == "__main__":
    main()
