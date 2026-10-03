"""자료 상태 점검. 수집·분류 결과에 빠진 것이 없는지 세 가지로 본다. AI를 부르지 않는다.

1. 완전성      필수 값이 빈 리뷰·분류 결과의 비율
2. 일관성      Steam 추천 여부와 AI가 읽은 반응이 정반대인 비율 (오답이라는 뜻은 아니다)
3. 분석 가능성  너무 짧은 글과 "판단 불가" 응답의 비율. 분석 대상인데 결과가 없는 글이 있으면 그만큼 깎는다
   (저장 파일의 키 이름은 예전 그대로 accuracy다. 정답과 견준 정확도가 아니다)

AI 정답률이나 통계적 신뢰도를 재는 점수가 아니라, 다시 돌려야 할 만큼 자료가 비었는지 보는 내부 기준이다.
화면 집계(dashboard_evidence)와 같은 파일을 같은 함수로 읽는다.

화면을 열 때마다 다시 계산한다(파일로 저장하지 않는다). 직접 보기: python quality_check.py
"""

import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import cfg
import dashboard_evidence as evidence

WEIGHTS = {"completeness": 0.375, "consistency": 0.3125, "accuracy": 0.3125}
PASS_THRESHOLD = 80
WARN_THRESHOLD = 60
QUALITY_NOTE = "내부 자료 상태 점수입니다. 분석 누락을 다른 점수로 가리지 않도록 완료율로 상한을 둡니다. AI 정답률이나 통계적 신뢰도는 아닙니다."


def score_completeness(reviews, analyzed):
    """필수 값이 빈 비율. 결측 1%마다 10점을 깎는다."""
    if not reviews:
        return {"score": 0, "issues": ["수집한 리뷰가 없습니다"], "n_reviews": 0, "n_analysis": 0}
    required = ("recommendationid", "content", "voted_up", "playtime_at_review_min")
    miss_review = sum(any(r.get(k) in (None, "") for k in required) for r in reviews.values()) / len(reviews) * 100
    miss_analysis = (sum(not a.get("k") or a.get("s") not in ("P", "N", "M", "U") for a in analyzed.values())
                     / len(analyzed) * 100) if analyzed else 100
    issues = []
    if miss_review > 5:
        issues.append(f"리뷰 필수 값 결측 {miss_review:.1f}% (기준 5% 이내)")
    if miss_analysis > 5:
        issues.append(f"분류 결과 결측 {miss_analysis:.1f}% (기준 5% 이내)")
    return {"score": round(max(0, 100 - (miss_review + miss_analysis) / 2 * 10), 1),
            "n_reviews": len(reviews), "n_analysis": len(analyzed),
            "missing_review_pct": round(miss_review, 2), "missing_analysis_pct": round(miss_analysis, 2),
            "issues": issues}


def score_consistency(reviews, analyzed):
    """추천했는데 부정, 비추천했는데 긍정으로 읽힌 비율. 혼합은 어긋난 것으로 치지 않고, 판단 불가는 뺀다."""
    checked = [(evidence.is_positive(reviews[rid]), a.get("s")) for rid, a in analyzed.items() if a.get("s") in ("P", "N", "M")]
    if not checked:
        return {"score": 0, "issues": ["반응이 분류된 리뷰가 없습니다"], "checked": 0, "inconsistent": 0, "inconsistent_pct": 100}
    inconsistent = sum((up and s == "N") or (not up and s == "P") for up, s in checked)
    pct = inconsistent / len(checked) * 100
    return {"score": round(max(0, 100 - pct * 3), 1), "checked": len(checked), "inconsistent": inconsistent,
            "inconsistent_pct": round(pct, 2),
            "issues": [f"추천 여부와 반응이 반대인 리뷰 {pct:.1f}% (기준 15% 이내)"] if pct > 15 else []}


def score_accuracy(reviews, analyzed, design):
    """짧아서 뺀 글과 판단 불가 응답이 많으면 깎는다. 분석 대상인데 결과가 없는 글이 있으면 완료율이 상한이다."""
    if not reviews or not analyzed:
        return {"score": 0, "issues": ["데이터 부족"], "analysis_completion_pct": 0}
    eligible = evidence.eligible_ids(reviews, design)
    missing = len(eligible - analyzed.keys())
    completion = (len(eligible) - missing) / len(eligible) * 100 if eligible else 100
    short = len(reviews) - len(eligible)
    filter_pct = short / len(reviews) * 100
    neutral_pct = sum(a.get("s") not in ("P", "N", "M") for a in analyzed.values()) / len(analyzed) * 100
    filter_score = max(0, 100 - max(0, filter_pct - 30) * 2)      # 30%까지는 감점 없음
    neutral_score = max(0, 100 - max(0, neutral_pct - 10) * 3)    # 10%까지는 감점 없음
    issues = []
    if neutral_pct > 30:
        issues.append(f"판단 불가 응답 {neutral_pct:.1f}% (기준 30% 이내)")
    if filter_pct > 50:
        issues.append(f"너무 짧아 뺀 리뷰 {filter_pct:.1f}%")
    if missing:
        issues.append(f"분석 가능한 리뷰 {missing}건이 AI 결과에서 누락됨")
    return {"score": round(min((filter_score + neutral_score) / 2, completion), 1),
            "short_filtered": short, "missing_eligible": missing, "analysis_completion_pct": round(completion, 2),
            "filter_pct": round(filter_pct, 2), "neutral_response_pct": round(neutral_pct, 2), "issues": issues}


def rescore(report):
    """세 점수에서 종합 점수와 등급을 낸다."""
    dims = (report or {}).get("dimensions")
    if not dims:
        return report
    dims = {key: dims[key] for key in WEIGHTS if key in dims}
    overall = sum((dims.get(key) or {}).get("score", 0) * weight for key, weight in WEIGHTS.items())
    completion = (dims.get("accuracy") or {}).get("analysis_completion_pct")
    if completion is not None:
        overall = min(overall, completion)   # 분석이 3분의 1 빠졌는데 다른 점수로 "통과"가 되지 않게 한다
    grade, grade_kr = (("PASS", "통과") if overall >= PASS_THRESHOLD
                       else ("WARN", "주의") if overall >= WARN_THRESHOLD else ("FAIL", "실패"))
    return {"overall_score": round(overall, 1), "grade": grade, "grade_kr": grade_kr,
            "thresholds": {"pass": PASS_THRESHOLD, "warn": WARN_THRESHOLD},
            "weights": WEIGHTS, "dimensions": dims, "metric_note": QUALITY_NOTE}


def run_quality_check(directory=None):
    source = evidence.load_sources(directory or cfg.project_dir())
    reviews, analyzed, design = source[:3] if source else ({}, {}, {})
    return rescore({"dimensions": {"completeness": score_completeness(reviews, analyzed),
                                   "consistency": score_consistency(reviews, analyzed),
                                   "accuracy": score_accuracy(reviews, analyzed, design)}})


def main():
    report = run_quality_check()
    print(f"자료 상태 점검 — 종합 {report['overall_score']:.1f}점 [{report['grade_kr']}]")
    for key, name in (("completeness", "완전성"), ("consistency", "일관성"), ("accuracy", "분석 가능성")):
        dim = report["dimensions"][key]
        print(f"  {name} {dim['score']:.1f}점" + "".join(f"\n    · {issue}" for issue in dim["issues"]))
    return report


if __name__ == "__main__":
    main()
