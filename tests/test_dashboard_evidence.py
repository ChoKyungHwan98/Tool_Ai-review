import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_evidence import build_evidence, evidence_page, review_hours, stage_profile
from main import review_rows
import analysis_design


class DashboardEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.rows = [
            {"recommendationid": "1", "content": "좋지만 저장이 안 됩니다", "voted_up": "1",
             "playtime_at_review_min": "0", "playtime_forever_min": "10000", "votes_up": "2"},
            {"recommendationid": "2", "content": "저장 오류", "voted_up": "0",
             "playtime_at_review_min": "120", "playtime_forever_min": "120", "votes_up": "5"},
            {"recommendationid": "3", "content": "좋음", "voted_up": "1",
             "playtime_at_review_min": "", "playtime_forever_min": "6000", "votes_up": "0"},
            {"recommendationid": "4", "content": "재미있다", "voted_up": "1",
             "playtime_at_review_min": "6000", "playtime_forever_min": "6000", "votes_up": "0"},
        ]
        self.analyses = [
            {"id": "1", "up": 0, "s": "M", "t": [["저장", "N"], ["저장", "N"], ["건축", "P"], ["건축", "N"]], "f": ["몰두", "몰두"]},
            {"id": "2", "s": "N", "t": [["저장", "N"]]},
            {"id": "4", "s": "P", "t": [["건축", "P"]], "f": ["도전"]},
        ]
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        with (self.folder / "reviews.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        (self.folder / "analysis_v3.jsonl").write_text("\n".join(json.dumps(a) for a in self.analyses), encoding="utf-8")

    def test_zero_hours_is_not_replaced_by_lifetime_hours(self):
        self.assertEqual(review_hours(self.rows[0]), 0)
        self.assertIsNone(review_hours(self.rows[2]))
        e = build_evidence(self.folder, 42)
        self.assertEqual([c["n"] for c in e["cohorts"]], [1, 1, 0, 1])
        self.assertEqual(e["counts"]["unknown_playtime"], 1)

    def test_even_sized_stage_uses_arithmetic_median(self):
        profile = stage_profile("all", "all", ["1", "2"], {r["recommendationid"]: r for r in self.rows})
        self.assertEqual(profile["hours"], 1.0)

    def test_review_cards_use_complete_source_without_analysis_csv(self):
        self.rows[0]["content"] = "긴 원문 " * 100
        self.rows[0]["language"] = "koreana"
        for row in self.rows[1:]:
            row["language"] = "koreana"
        self.analyses[0]["k"] = "저장 개선 필요"
        self.write()
        cards = review_rows(self.folder)
        self.assertEqual(len(cards), 3)
        self.assertEqual(cards[0]["content"], self.rows[0]["content"])
        self.assertEqual(cards[0]["playtime_h"], 0)
        self.assertEqual(cards[0]["overall_sentiment"], "MIXED")
        self.assertEqual(cards[0]["keywords"], "저장|건축")
        self.assertEqual(cards[0]["language"], "koreana")

    def test_topic_counts_are_unique_and_distinct_from_recommendation(self):
        e = build_evidence(self.folder, 42)
        self.assertEqual(e["mood"], {"P": 1, "M": 1, "N": 1, "U": 0})
        storage = next(t for t in e["themes"] if t["name"] == "저장")
        building = next(t for t in e["themes"] if t["name"] == "건축")
        self.assertEqual(storage["neg"], 2)
        self.assertEqual(storage["negative_recommended"], 1)
        self.assertEqual(building["mentions"], 2)
        self.assertEqual(building["pos"] + building["neg"], 3)
        # 건축: 불만 1 / (칭찬 2 + 불만 1). 범위가 50%를 걸치므로 어느 쪽이 많은지 말하지 않는다.
        self.assertLess(building["neg_range"][0], 50)
        self.assertGreater(building["neg_range"][1], 50)
        self.assertFalse(building["sure"])
        self.assertEqual(e["counts"]["complaint_reviews"], 2)
        self.assertEqual(e["counts"]["recommended_complaints"], 1)

    def test_heatmap_and_fun_use_their_own_denominators(self):
        e = build_evidence(self.folder, 42)
        storage = next(t for t in e["themes"] if t["name"] == "저장")
        self.assertEqual(storage["cells"][0], {"count": 1, "denominator": 1, "rate": 100.0})
        self.assertIsNone(storage["cells"][2]["rate"])
        self.assertEqual(e["fun_denominator"], 2)
        self.assertTrue(all(f["share"] == 50 for f in e["fun"]))

    def test_evidence_pagination_and_sentiment_match_counts(self):
        page = evidence_page(self.folder, 42, "저장", "N")
        self.assertEqual(page["total"], 2)
        self.assertEqual([r["id"] for r in page["reviews"]], ["2", "1"])
        self.assertEqual(evidence_page(self.folder, 42, "저장", "P")["total"], 0)
        self.assertEqual(evidence_page(self.folder, 42, "저장", "N", 2)["reviews"], [])
        self.assertIsNone(evidence_page(self.folder, 42, "없는 주제"))

    def test_partial_lines_and_unmatched_ids_are_reported(self):
        with (self.folder / "analysis_v3.jsonl").open("a", encoding="utf-8") as f:
            f.write('\n{"id":"missing"}\n{"id":')
        e = build_evidence(self.folder, 42)
        self.assertEqual(e["counts"]["skipped_analysis"], 2)
        self.assertEqual(e["counts"]["analyzed"], 3)

    def test_deep_analysis_recounts_saved_results_without_ai(self):
        complaints = [
            {"id": "1", "p": [{"t": "저장", "prob": "세이브 파일이 날아감", "why": "서버 오류", "fix": "자동 저장 추가해주세요"}]},
            {"id": "2", "p": [{"t": "저장", "prob": "세이브 파일 손상", "why": "", "fix": "자동 저장 추가해주세요"},
                              {"t": "저장", "prob": "", "why": "", "fix": "버그 수정"}]},
        ]
        (self.folder / "complaints_v3.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in complaints), encoding="utf-8")
        deep = build_evidence(self.folder, 42)["deep"]
        storage = next(c for c in deep["causes"] if c["theme"] == "저장")
        self.assertEqual(storage["reviews"], 2)
        self.assertEqual(storage["terms"][0]["word"], "세이브")
        self.assertEqual({t["word"] for t in storage["terms"]}, {"세이브", "파일"})
        # 2번만 비추천이고 2시간 플레이 → 초반 이탈
        self.assertEqual(deep["churn"]["early_n"], 1)
        self.assertEqual(deep["churn"]["topics"][0], {"name": "저장", "early": 1, "later": 0, "early_share": 100.0, "later_share": None})
        agreed = next(t for t in deep["agreed"]["topics"] if t["name"] == "저장")
        self.assertEqual(agreed["votes"], 7)
        self.assertEqual(deep["agreed"]["reviews"][0]["id"], "2")
        self.assertEqual(deep["wants"], [{"text": "자동 저장 추가해주세요", "theme": "저장", "count": 2, "votes": 7}])

    def test_deep_analysis_without_complaint_file(self):
        deep = build_evidence(self.folder, 42)["deep"]
        self.assertFalse(deep["has_complaints"])
        self.assertEqual(deep["wants"], [])
        self.assertTrue(all(c["terms"] == [] for c in deep["causes"]))

    def test_topics_ai_split_are_merged_by_rule(self):
        # '세이브'는 '저장'과 같은 리뷰·같은 불만에만 붙는다 → 규칙으로 합친다
        self.rows.append({"recommendationid": "5", "content": "세이브 날아감", "voted_up": "0",
                          "playtime_at_review_min": "30", "playtime_forever_min": "30", "votes_up": "1"})
        self.analyses = [
            {"id": "1", "s": "N", "t": [["저장", "N"], ["세이브", "N"], ["건축", "P"]]},
            {"id": "2", "s": "N", "t": [["저장", "N"], ["세이브", "N"]]},
            {"id": "5", "s": "N", "t": [["저장", "N"], ["세이브", "N"]]},
            {"id": "3", "s": "N", "t": [["저장", "N"]]},
            {"id": "4", "s": "P", "t": [["건축", "P"], ["건 축", "P"]]},
        ]
        self.write()
        e = build_evidence(self.folder, 42)
        self.assertEqual(sorted(t["name"] for t in e["themes"]), ["건축", "저장"])
        storage = next(t for t in e["themes"] if t["name"] == "저장")
        self.assertEqual((storage["mentions"], storage["neg"]), (4, 4))
        self.assertEqual(e["n_ai_topics"], 4)
        self.assertEqual({(m["from"], m["to"], m["overlap"]) for m in e["merges"]},
                         {("세이브", "저장", 100), ("건 축", "건축", None)})
        self.assertIsNone(evidence_page(self.folder, 42, "세이브"))
        self.assertEqual(evidence_page(self.folder, 42, "저장", "N")["total"], 4)

    def test_topics_that_only_share_some_reviews_stay_apart(self):
        members = {"렉": {"P": set(), "N": {"1", "2", "3", "4", "5"}},
                   "최적화": {"P": {"6"}, "N": {"1", "2", "3", "9"}},
                   "사운드": {"P": {"1", "2"}, "N": set()}}
        alias, merges = analysis_design.auto_merge(members)
        self.assertEqual(alias, {})            # 최적화 불만 3/4 = 75%, 사운드는 3건 미만
        members["최적화"] = {"P": {"1", "2", "6"}, "N": {"1", "2", "3"}}
        alias, merges = analysis_design.auto_merge(members)
        self.assertEqual(alias, {})            # 같은 리뷰라도 칭찬으로 붙은 것은 겹침으로 세지 않는다
        members["최적화"] = {"P": set(), "N": {"1", "2", "3", "4"}}
        alias, merges = analysis_design.auto_merge(members)
        self.assertEqual(alias, {"최적화": "렉"})
        self.assertEqual(merges[0]["overlap"], 100)

    def test_design_log_names_who_decided_each_step(self):
        themes = [{"name": "저장", "pos": 0, "neg": 2}, {"name": "건축", "pos": 2, "neg": 1}]
        rows = analysis_design.design_log({"params": {"language": "koreana", "since": "2026-07-13", "sort": "recent",
                                                      "target_error_pct": 5}, "design": {"n_total": 400}},
                                          {"collected": 380, "analyzed": 300}, {"model": "m"}, 12,
                                          [{"from": "세이브", "to": "저장", "overlap": 92, "reason": ""}], themes, "팰월드")
        by_step = {r["step"]: r for r in rows}
        self.assertEqual(by_step["무엇을"]["text"], "팰월드 · 한국어 · 2026.07.13 이후 · 최신순")
        self.assertEqual(by_step["얼마나"]["text"], "400건 계획 (표본 크기를 정한 기준 ±5%) → 380건 수집")
        self.assertEqual([by_step[k]["who"] for k in ("무엇을", "얼마나", "분석 모델", "주제 제안", "주제 합치기", "강조한 주제")],
                         ["사람", "사람", "사람", "AI", "규칙", "규칙"])
        self.assertEqual(by_step["주제 합치기"]["details"], ["세이브 → 저장 (겹침 92%)"])
        self.assertEqual(by_step["강조한 주제"]["text"], "칭찬 최다 = 건축 (칭찬 2건) · 불만 최다 = 저장 (불만 2건)")

    def test_empty_and_missing_sources_are_explicit(self):
        self.analyses = []
        self.write()
        e = build_evidence(self.folder, 42)
        self.assertEqual(e["themes"], [])
        self.assertEqual(e["fun"], [])
        self.assertEqual(e["mood"], {"P": 0, "M": 0, "N": 0, "U": 0})
        self.assertEqual(e["counts"]["complaint_reviews"], 0)
        self.assertIsNone(build_evidence(self.folder / "absent", 42))


if __name__ == "__main__":
    unittest.main()
