import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
import main
import pipeline


class StoppedRunTests(unittest.TestCase):
    """분석이 멈췄을 때: 무엇이 남았는지 알려 주고, 이어서 할지 지울지는 사람이 정한다."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / "7"
        self.folder.mkdir()
        self.patch = patch.object(main.cfg, "PROJECTS_DIR", str(self.root))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def write_run(self, status, **extra):
        record = {"status": status, "started_at": "2026-10-04T01:00:00", "finished_at": None, "steps": {},
                  "request": {"app_id": 7, "lang": "koreana", "sort": "random", "model": "m"}, **extra}
        (self.folder / "pipeline_result.json").write_text(json.dumps(record), encoding="utf-8")

    def test_kept_work_counts_what_is_left_in_the_folder(self):
        (self.folder / "scan_state.json").write_text(json.dumps({"count": 1400, "cursor": "c"}), encoding="utf-8")
        (self.folder / "progress.json").write_text(json.dumps({"stage": "collect", "done": 1400, "total": 2400}), encoding="utf-8")
        self.assertEqual(main.kept_work(str(self.folder)), {"collected": 0, "classified": 0, "deep": 0, "themes": False,
                                                            "scanned": 1400, "scan_total": 2400})
        (self.folder / "reviews.csv").write_text("recommendationid,content\n1,a\n2,b\n3,c\n", encoding="utf-8-sig")
        (self.folder / "analysis_v3.jsonl").write_text('{"id":"1"}\n{"id":"2"}\n', encoding="utf-8")
        kept = main.kept_work(str(self.folder))
        self.assertEqual((kept["collected"], kept["classified"], kept["scanned"]), (3, 2, 0))   # 다 모았으면 훑던 기록은 세지 않는다

    def test_stopped_run_reports_its_settings_and_what_is_kept(self):
        self.write_run("failed", error="Steam이 요청을 계속 거절합니다")
        (self.folder / "reviews.csv").write_text("recommendationid,content\n1,a\n", encoding="utf-8-sig")
        result = main.pipeline_last_result(7)
        self.assertEqual(result["request"]["sort"], "random")     # "이어서 하기"가 같은 설정으로 다시 시작하는 데 쓴다
        self.assertEqual(result["kept"]["collected"], 1)

    def test_run_cut_off_by_a_restart_is_reported_as_stopped(self):
        self.write_run("running")
        with patch.dict(pipeline.RUNNING, {"app_id": None}):
            result = main.pipeline_last_result(7)
            self.assertEqual(result["status"], "failed")
            self.assertIn("꺼지면서", result["error"])
        with patch.dict(pipeline.RUNNING, {"app_id": 7}):
            self.assertEqual(main.pipeline_last_result(7)["status"], "running")

    def test_looking_up_a_game_does_not_create_a_folder(self):
        self.assertEqual(main.pipeline_last_result(999)["status"], "no_runs")
        self.assertFalse((self.root / "999").exists())

    def test_unfinished_project_can_be_discarded_even_though_it_is_not_listed(self):
        (self.folder / "reviews.csv").write_text("recommendationid\n1\n", encoding="utf-8-sig")
        with patch.object(main, "_load_games", return_value=[]), patch.object(main, "_save_games"):
            with patch.dict(pipeline.RUNNING, {"app_id": 7}):
                with self.assertRaises(HTTPException) as running:
                    main.trash_game(7)
                self.assertEqual(running.exception.status_code, 409)    # 돌고 있는 분석은 지우지 않는다
            main.trash_game(7)
            self.assertFalse(self.folder.exists())
            moved = os.listdir(self.root / ".review-trash")
            self.assertEqual(len(moved), 1)                              # 지우지 않고 휴지통 폴더로 옮긴다
            with self.assertRaises(HTTPException) as missing:
                main.trash_game(7)
            self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
