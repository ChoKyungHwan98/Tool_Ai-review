import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze_reviews_v3
import build_insights_v5
import quality_check


class MathAuditTests(unittest.TestCase):
    def test_missing_review_playtime_does_not_become_lifetime_playtime(self):
        self.assertIsNone(analyze_reviews_v3.hours({"playtime_forever_min": "6000"}))
        self.assertIsNone(build_insights_v5.hours({"playtime_forever_min": "6000"}))
        self.assertEqual(analyze_reviews_v3.hours({"playtime_at_review_min": "0", "playtime_forever_min": "6000"}), 0)

    def test_unmeasured_ai_confidence_is_not_fabricated(self):
        row = analyze_reviews_v3.analysis_row(
            {"recommendationid": "1", "voted_up": "1", "content": "재미있다", "playtime_at_review_min": "30"},
            {"s": "P", "t": [], "k": "재미"}, {})
        self.assertEqual(row["confidence"], "")
        self.assertEqual(row["needs_verification"], "")

    def test_theme_association_compares_themed_reviews_only(self):
        reviews = [{"recommendationid": str(i), "content": "충분히 긴 리뷰", "voted_up": "0" if i in (4, 5, 6) else "1",
                    "playtime_at_review_min": "60", "votes_up": "0"} for i in range(1, 23)]
        analyzed = [{"id": str(i), "up": 0 if i in (4, 5, 6) else 1,
                     "s": "N" if i in (4, 5, 6) else "P", "t": [["A" if i <= 4 else "B", "N" if i in (4, 5, 6) else "P"]], "f": []}
                    for i in range(1, 7)]
        themes = [{"name": "A", "desc": ""}, {"name": "B", "desc": ""}]
        with patch.object(build_insights_v5, "load", return_value=(reviews, analyzed, [], themes, {})):
            result, _ = build_insights_v5.build()
        by_name = {t["name"]: t for t in result["themes"]}
        self.assertGreater(by_name["A"]["impact"], 0)
        self.assertLess(by_name["B"]["impact"], 0)

    def test_quality_component_stays_within_score_range(self):
        reviews = [{"recommendationid": str(i)} for i in range(100)]
        analysis = [{"overall_sentiment": "POSITIVE"}]
        self.assertGreaterEqual(quality_check.score_accuracy(reviews, analysis)["score"], 0)
        self.assertLessEqual(quality_check.score_accuracy(reviews, analysis)["score"], 100)

    def test_missing_ai_outputs_are_not_counted_as_short_reviews(self):
        reviews = [{"recommendationid": str(i), "content": "충분히 긴 리뷰"} for i in range(4)]
        analysis = [{"recommendationid": "0", "overall_sentiment": "POSITIVE"}]
        result = quality_check.score_accuracy(reviews, analysis)
        self.assertEqual(result["short_filtered"], 0)
        self.assertEqual(result["missing_eligible"], 3)
        self.assertEqual(result["analysis_completion_pct"], 25)
        self.assertLessEqual(result["score"], 25)


if __name__ == "__main__":
    unittest.main()
