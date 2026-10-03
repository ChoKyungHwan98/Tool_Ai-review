import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import main


class DesignApiTests(unittest.TestCase):
    """자동으로 합친 주제가 화면 데이터(설명·할 일·리뷰 태그)와 분석 설계서에 그대로 반영되는지."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        rows = [{"recommendationid": str(i), "content": "렉이 심함", "voted_up": "0",
                 "playtime_at_review_min": "60", "votes_up": "1"} for i in (1, 2, 3)]
        with (self.folder / "reviews.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        analyses = [{"id": "1", "s": "N", "t": [["최적화", "N"], ["렉", "N"]]},
                    {"id": "2", "s": "N", "t": [["최적화", "N"], ["렉", "N"]]},
                    {"id": "3", "s": "N", "t": [["최적화", "N"], ["렉", "N"]]}]
        (self.folder / "analysis_v3.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in analyses), encoding="utf-8")
        (self.folder / "insights_v5.json").write_text(json.dumps({
            "themes": [{"name": "렉", "desc": "끊김"}, {"name": "최적화", "desc": "성능"}],
            "actions": [{"theme": "렉", "prob": "끊김"}], "usage": {"model": "m"}}, ensure_ascii=False), encoding="utf-8")
        self.patch = patch.object(main, "_game_dir", return_value=str(self.folder))
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_auto_merge_reaches_every_part_of_the_screen(self):
        data = self.client.get("/dashboard/data/v5?app_id=7").json()
        self.assertEqual([t["name"] for t in data["evidence"]["themes"]], ["렉"])  # 같은 크기면 이름순으로 남긴다
        self.assertEqual([t["name"] for t in data["themes"]], ["렉"])
        self.assertEqual(data["actions"][0]["theme"], "렉")
        self.assertEqual(data["reviews"][0]["keywords"], "렉")
        self.assertEqual(data["quality_report"]["dimensions"]["accuracy"]["analysis_completion_pct"], 100)   # 저장 파일 없이 바로 계산
        self.assertNotIn("playtime", data)
        steps = {row["step"]: row for row in data["design_log"]}
        self.assertEqual(steps["주제 합치기"]["details"], ["최적화 → 렉 (겹침 100%)"])
        self.assertEqual(steps["강조한 주제"]["text"], "불만 최다 = 렉 (불만 3건)")
        self.assertNotIn("decisions", data)

    def test_analysis_export_is_built_from_the_current_classification(self):
        text = self.client.get("/api/analysis/download?app_id=7").content.decode("utf-8-sig")
        rows = list(csv.DictReader(text.splitlines()))
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[0]["sentiment"], rows[0]["topics"]), ("NEGATIVE", "최적화:N|렉:N"))

    def test_manual_edit_endpoints_are_gone(self):
        self.assertEqual(self.client.post("/dashboard/topics", json={}).status_code, 404)
        self.assertEqual(self.client.post("/dashboard/verdict", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
