import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_insights_v5
import quality_check


class MathAuditTests(unittest.TestCase):
    def test_theme_association_compares_themed_reviews_only(self):
        # 22건 중 주제가 붙은 글은 6건(비추천 3). 수집 전체와 견주면 두 주제 모두 "비추천 연관"으로 보인다.
        reviews = {str(i): {"recommendationid": str(i), "content": "충분히 긴 리뷰", "voted_up": "0" if i in (4, 5, 6) else "1",
                            "playtime_at_review_min": "60", "votes_up": "0"} for i in range(1, 23)}
        analyzed = {str(i): {"id": str(i), "s": "N" if i in (4, 5, 6) else "P",
                             "t": [["A" if i <= 4 else "B", "N" if i in (4, 5, 6) else "P"]], "f": []}
                    for i in range(1, 7)}
        themes = [{"name": "A", "desc": ""}, {"name": "B", "desc": ""}]
        with patch.object(build_insights_v5, "load", return_value=(reviews, analyzed, [], themes, {})):
            result, _ = build_insights_v5.build()
        by_name = {t["name"]: t for t in result["themes"]}
        self.assertGreater(by_name["A"]["impact"], 0)
        self.assertLess(by_name["B"]["impact"], 0)

    def test_quality_score_stays_in_range_and_is_capped_by_missing_analysis(self):
        reviews = {str(i): {"recommendationid": str(i), "content": "충분히 긴 리뷰", "voted_up": "1",
                            "playtime_at_review_min": "60"} for i in range(4)}
        analyzed = {"0": {"id": "0", "s": "P", "k": "만족"}}
        result = quality_check.score_accuracy(reviews, analyzed, {})
        self.assertEqual((result["short_filtered"], result["missing_eligible"], result["analysis_completion_pct"]), (0, 3, 25))
        self.assertLessEqual(result["score"], 25)
        report = quality_check.rescore({"dimensions": {
            "completeness": quality_check.score_completeness(reviews, analyzed),
            "consistency": quality_check.score_consistency(reviews, analyzed),
            "accuracy": result}})
        self.assertEqual(report["overall_score"], 25)   # 다른 점수가 만점이어도 빠진 만큼만 받는다
        self.assertEqual(report["grade"], "FAIL")
        # 거의 다 짧은 글이어도 점수는 0 아래로 내려가지 않는다
        short = {str(i): {"recommendationid": str(i), "content": "ㅋ" if i else "충분히 긴 리뷰", "voted_up": "1"} for i in range(100)}
        self.assertGreaterEqual(quality_check.score_accuracy(short, analyzed, {})["score"], 0)

    def test_quality_consistency_ignores_mixed_and_unknown(self):
        reviews = {str(i): {"voted_up": "1"} for i in range(4)}
        analyzed = {"0": {"s": "N"}, "1": {"s": "M"}, "2": {"s": "U"}, "3": {"s": "P"}}
        result = quality_check.score_consistency(reviews, analyzed)
        self.assertEqual((result["checked"], result["inconsistent"]), (3, 1))

if __name__ == "__main__":
    unittest.main()
