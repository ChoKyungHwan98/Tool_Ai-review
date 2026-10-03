"""데이터 품질 검증 (강의 session-16 표준)

수집 → 전처리 → 분석 단계에서 데이터 품질을 점검하여 점수화한다.
면접에서 "분석 결과를 어떻게 신뢰하는가?"에 대한 정량적 답변.

평가 4축 (각 100점, 가중치 합산):
1. 완전성 (Completeness)  ─ 결측치/빈 필드 비율
2. 일관성 (Consistency)    ─ voted_up과 sentiment 라벨 정합성
3. 대표성 (Representativeness) ─ 표본 비율 vs 모집단 비율 일치도
4. 분석 가능성              ─ 짧은 리뷰/노이즈 제거 후 비율 + 중립 응답 비율
   (정답과 견준 정확도가 아니다. 저장 파일의 키 이름 accuracy는 그대로 둔다)

산출:
- 콘솔: 4축 점수 + 종합 점수 (PASS ≥ 80, WARN 60-79, FAIL < 60)
- cfg.QUALITY_JSON: 대시보드용 JSON

실행: python quality_check.py
"""

import csv
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import json
import os
from collections import Counter

from config import cfg

def get_reviews_csv(): return cfg.REVIEWS_CSV
def get_analysis_csv(): return cfg.ANALYSIS_CSV

# Fetch positive rate from population stats dynamically
def get_pop_pos_rate():
    try:
        from main import review_population_stats
        stats = review_population_stats(cfg.APP_ID, cfg.LANG)["selected"]
        return stats["positive"] / stats["total"] if stats["total"] > 0 else None
    except Exception:
        return None


# ─── 가중치 (4축 100점) ────────────────────────────────────────────────
# 대표성은 점수에 넣지 않는다. 수집할 때 Steam 전체의 추천·비추천 비율에 맞춰 건수를 나누므로
# 그 비율이 맞는지 다시 재면 언제나 만점에 가깝게 나온다. 수치는 참고용으로만 남긴다.
WEIGHTS = {
    "completeness": 0.375,
    "consistency": 0.3125,
    "representativeness": 0.0,
    "accuracy": 0.3125,
}
QUALITY_NOTE = "내부 자료 상태 점수입니다. 분석 누락을 다른 점수로 가리지 않도록 완료율로 상한을 둡니다. AI 정답률이나 통계적 신뢰도는 아닙니다."
REPRESENTATIVENESS_NOTE = "추천 비율 비교만으로 표본 대표성을 검증할 수 없습니다. 시기·내용의 선택 편향은 남습니다"
CONSISTENCY_NOTE = "추천 여부와 글의 감성이 다를 수 있으므로 불일치가 AI 오답을 뜻하지 않습니다"


def rescore(report):
    """저장된 보고서의 종합 점수를 지금 가중치로 다시 계산한다(예전 가중치로 저장된 프로젝트용)."""
    dims = (report or {}).get("dimensions")
    if not dims:
        return report
    dims = {key: dict(value) for key, value in dims.items()}
    if "representativeness" in dims:
        dims["representativeness"]["note"] = REPRESENTATIVENESS_NOTE
    if "consistency" in dims:
        dims["consistency"]["note"] = CONSISTENCY_NOTE
    overall = sum((dims.get(key) or {}).get("score", 0) * weight for key, weight in WEIGHTS.items())
    completion = (dims.get("accuracy") or {}).get("analysis_completion_pct")
    if completion is not None:
        overall = min(overall, completion)
    grade, grade_kr = (("PASS", "통과") if overall >= PASS_THRESHOLD
                       else ("WARN", "주의") if overall >= WARN_THRESHOLD else ("FAIL", "실패"))
    return {**report, "overall_score": round(overall, 1), "grade": grade,
            "grade_kr": grade_kr, "weights": WEIGHTS, "dimensions": dims,
            "metric_note": QUALITY_NOTE}

# ─── 임계값 ────────────────────────────────────────────────────────────
PASS_THRESHOLD = 80
WARN_THRESHOLD = 60


def load_csv(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ─── 1. 완전성 ─────────────────────────────────────────────────────────
def score_completeness(reviews: list, analysis: list) -> dict:
    """필수 필드 결측 비율 → 100점에서 차감"""
    issues = []
    if not reviews:
        return {"score": 0, "issues": ["reviews.csv 없음"], "n_reviews": 0, "n_analysis": 0}

    # 리뷰 필수 필드 (Steam API 원본 컬럼명)
    required_review = ["recommendationid", "content", "voted_up", "playtime_at_review_min"]
    missing_review = sum(1 for r in reviews if any(not r.get(k) for k in required_review))
    miss_review_pct = missing_review / len(reviews) * 100

    # 분석 필수 필드
    required_analysis = ["overall_sentiment", "key_phrase"]
    missing_analysis = sum(1 for r in analysis if any(not r.get(k) for k in required_analysis))
    miss_analysis_pct = missing_analysis / len(analysis) * 100 if analysis else 100

    if miss_review_pct > 5:
        issues.append(f"리뷰 필수 필드 결측 {miss_review_pct:.1f}% (기준 5% 이내)")
    if miss_analysis_pct > 5:
        issues.append(f"분석 필수 필드 결측 {miss_analysis_pct:.1f}% (기준 5% 이내)")

    # 점수: 결측 비율의 평균을 100점 만점에서 차감
    avg_miss = (miss_review_pct + miss_analysis_pct) / 2
    score = max(0, 100 - avg_miss * 10)  # 결측 1% = -10점

    return {
        "score": round(score, 1),
        "n_reviews": len(reviews),
        "n_analysis": len(analysis),
        "missing_review_pct": round(miss_review_pct, 2),
        "missing_analysis_pct": round(miss_analysis_pct, 2),
        "issues": issues,
    }


# ─── 2. 일관성 ─────────────────────────────────────────────────────────
def score_consistency(analysis: list) -> dict:
    """voted_up과 overall_sentiment 라벨 일치 여부 점검. 불일치가 곧 오답은 아니다.

    규칙:
    - voted_up=1 → sentiment가 POSITIVE/MIXED여야 정상 (NEGATIVE면 불일치)
    - voted_up=0 → sentiment가 NEGATIVE/MIXED여야 정상 (POSITIVE면 불일치)
    - NEUTRAL은 일관성 검증에서 제외
    """
    issues = []
    if not analysis:
        return {"score": 0, "issues": ["analysis_v3.csv 없음"], "inconsistent_pct": 100}

    inconsistent = 0
    checked = 0
    for r in analysis:
        try:
            vu = int(r.get("voted_up", 0))
        except (ValueError, TypeError):
            continue
        sent = r.get("overall_sentiment", "")
        if sent == "NEUTRAL":
            continue
        checked += 1
        if vu == 1 and sent == "NEGATIVE":
            inconsistent += 1
        elif vu == 0 and sent == "POSITIVE":
            inconsistent += 1

    inconsistent_pct = inconsistent / checked * 100 if checked else 100
    if inconsistent_pct > 15:
        issues.append(f"voted_up과 감성 라벨 불일치 {inconsistent_pct:.1f}% (기준 15% 이내)")

    # 점수: 불일치 1% = -3점
    score = max(0, 100 - inconsistent_pct * 3)

    return {
        "score": round(score, 1),
        "checked": checked,
        "inconsistent": inconsistent,
        "inconsistent_pct": round(inconsistent_pct, 2),
        "issues": issues,
        "note": CONSISTENCY_NOTE,
    }


# ─── 3. 대표성 ─────────────────────────────────────────────────────────
def score_representativeness(reviews: list, population_pos_rate: float = None) -> dict:
    """표본의 추천/비추천 비율이 모집단과 얼마나 가까운가"""
    issues = []
    if not reviews:
        return {"score": 0, "issues": ["reviews.csv 없음"]}

    pos_count = sum(1 for r in reviews if r.get("voted_up") == "1")
    neg_count = sum(1 for r in reviews if r.get("voted_up") == "0")
    total = pos_count + neg_count
    if total == 0:
        return {"score": 0, "issues": ["voted_up 데이터 없음"]}

    sample_pos_rate = pos_count / total
    if population_pos_rate is None:
        population_pos_rate = get_pop_pos_rate()
    if population_pos_rate is None:
        return {"score": 0, "sample_pos_rate": round(sample_pos_rate * 100, 2),
                "population_pos_rate": None, "diff_pct_points": None,
                "issues": ["선택한 언어의 스팀 전체 리뷰 통계를 확인하지 못했습니다"],
                "note": "모집단과의 차이를 계산하지 않았습니다"}
    diff_pct = abs(sample_pos_rate - population_pos_rate) * 100  # %p

    # 추천 비율의 차이만 기록한다. 일치해도 시기·작성자·내용의 대표성은 검증되지 않는다.
    # 5%p 이내 = 거의 모집단 비율 → 100점
    score = max(0, 100 - diff_pct * 1.5)

    if diff_pct > 25:
        issues.append(f"표본 추천 비율 {sample_pos_rate*100:.1f}% vs 실제 {population_pos_rate*100:.1f}% → "
                      f"{diff_pct:.1f}%p 차이 (추천 비율만 가중 보정 가능)")

    return {
        "score": round(score, 1),
        "sample_pos_rate": round(sample_pos_rate * 100, 2),
        "population_pos_rate": round(population_pos_rate * 100, 2),
        "diff_pct_points": round(diff_pct, 2),
        "issues": issues,
        "note": REPRESENTATIVENESS_NOTE,
    }


# ─── 4. 분석 가능성 (키 이름은 accuracy) ─────────────────────────────────────────────────────────
def score_accuracy(reviews: list, analysis: list) -> dict:
    """짧은/노이즈 리뷰 제거 비율 + 중립 응답 비율로 LLM 분석 품질 추정"""
    issues = []
    if not reviews or not analysis:
        return {"score": 0, "issues": ["데이터 부족"]}

    # 실제 원문 길이로 짧은 글을 센다. AI 호출 실패·누락을 짧아서 제외한 글로 세면 안 된다.
    eligible = {str(r.get("recommendationid")) for r in reviews
                if len((r.get("content") or "").strip()) >= cfg.MIN_REVIEW_LEN and r.get("recommendationid")}
    analyzed_ids = {str(r.get("recommendationid")) for r in analysis if r.get("recommendationid")}
    short_filtered = sum(len((r.get("content") or "").strip()) < cfg.MIN_REVIEW_LEN for r in reviews)
    missing_eligible = len(eligible - analyzed_ids)
    completion_pct = (len(eligible & analyzed_ids) / len(eligible) * 100) if eligible else 100
    filter_pct = short_filtered / len(reviews) * 100

    # 중립 응답 비율 (LLM이 분류를 회피한 정도)
    sentiments = Counter(r.get("overall_sentiment", "") for r in analysis)
    neutral_pct = sentiments.get("NEUTRAL", 0) / len(analysis) * 100 if analysis else 100

    # 점수 계산
    # - 짧은 리뷰 제거: 30%를 넘으면 표본 효율 감점. 적게 걸러진 것은 감점 사유가 아니다.
    # - 중립 응답: < 10% 우수, > 30% 부진
    filter_score = max(0, 100 - max(0, filter_pct - 30) * 2)
    neutral_score = max(0, 100 - max(0, neutral_pct - 10) * 3)
    score = min((filter_score + neutral_score) / 2, completion_pct)

    if neutral_pct > 30:
        issues.append(f"중립 응답 {neutral_pct:.1f}% (기준 30% 이내) — LLM 분류 회피가 많음")
    if filter_pct > 50:
        issues.append(f"짧은 리뷰 제거 비율 {filter_pct:.1f}% — 표본 효율 낮음")
    if missing_eligible:
        issues.append(f"분석 가능한 리뷰 {missing_eligible}건이 AI 결과에서 누락됨")

    return {
        "score": round(score, 1),
        "short_filtered": short_filtered,
        "missing_eligible": missing_eligible,
        "analysis_completion_pct": round(completion_pct, 2),
        "filter_pct": round(filter_pct, 2),
        "neutral_response_pct": round(neutral_pct, 2),
        "issues": issues,
    }


# ─── 종합 ──────────────────────────────────────────────────────────────
def run_quality_check() -> dict:
    reviews = load_csv(get_reviews_csv())
    analysis = load_csv(get_analysis_csv())

    completeness = score_completeness(reviews, analysis)
    consistency = score_consistency(analysis)
    representativeness = score_representativeness(reviews)
    accuracy = score_accuracy(reviews, analysis)

    overall = (
        completeness["score"] * WEIGHTS["completeness"]
        + consistency["score"] * WEIGHTS["consistency"]
        + representativeness["score"] * WEIGHTS["representativeness"]
        + accuracy["score"] * WEIGHTS["accuracy"]
    )
    overall = min(overall, accuracy.get("analysis_completion_pct", 100))

    if overall >= PASS_THRESHOLD:
        grade, grade_kr = "PASS", "통과"
    elif overall >= WARN_THRESHOLD:
        grade, grade_kr = "WARN", "주의"
    else:
        grade, grade_kr = "FAIL", "실패"

    report = {
        "overall_score": round(overall, 1),
        "metric_note": QUALITY_NOTE,
        "grade": grade,
        "grade_kr": grade_kr,
        "thresholds": {"pass": PASS_THRESHOLD, "warn": WARN_THRESHOLD},
        "weights": WEIGHTS,
        "dimensions": {
            "completeness": completeness,
            "consistency": consistency,
            "representativeness": representativeness,
            "accuracy": accuracy,
        },
    }
    return report


def print_report(r: dict):
    print("=" * 64)
    print(f"데이터 품질 점검 — 종합 {r['overall_score']:.1f}점 [{r['grade_kr']}]")
    print("=" * 64)
    for key, kr in [("completeness", "완전성"), ("consistency", "일관성"),
                    ("representativeness", "대표성(점수 제외)"), ("accuracy", "분석 가능성")]:
        dim = r["dimensions"][key]
        w = r["weights"][key]
        print(f"\n[{kr}] {dim['score']:.1f}점  (가중치 {w*100:.0f}%)")
        for k, v in dim.items():
            if k in ("score", "issues", "note"):
                continue
            print(f"  · {k}: {v}")
        if dim.get("note"):
            print(f"  💬 {dim['note']}")
        if dim.get("issues"):
            for iss in dim["issues"]:
                print(f"  ⚠ {iss}")


def main():
    report = run_quality_check()
    print_report(report)
    with open(cfg.QUALITY_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] {cfg.QUALITY_JSON} 저장")


if __name__ == "__main__":
    main()
