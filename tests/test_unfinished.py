import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_reviews_v3 as analyzer
import main
import openrouter_limits as limits
import pipeline


class UnfinishedListTests(unittest.TestCase):
    """끝나지 못한 분석은 홈 목록에 따로 나온다. 뒤에 남은 것을 사람이 모르면 안 된다."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(main.cfg, "PROJECTS_DIR", str(self.root)),
                        patch.object(main, "_load_games", return_value=[{"app_id": 1, "name": "끝난 게임"}]),
                        patch.object(main, "search_steam_game", return_value={"name": "멈춘 게임", "header_image": "h"}),
                        patch.dict(main._GAME_INFO, {}, clear=True),
                        patch.dict(pipeline.RUNNING, {"app_id": None})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def project(self, app_id, status, finished=False):
        folder = self.root / str(app_id)
        folder.mkdir()
        (folder / "pipeline_result.json").write_text(json.dumps(
            {"status": status, "started_at": "2026-10-04T01:00:00", "finished_at": None, "steps": {},
             "error": "멈춘 이유" if status == "failed" else None,
             "request": {"app_id": app_id, "lang": "koreana", "sort": "random", "model": "m"}}), encoding="utf-8")
        if finished:
            (folder / "insights_v5.json").write_text("{}", encoding="utf-8")
        return folder

    def test_stopped_run_is_listed_with_what_is_kept(self):
        self.project(1, "done", finished=True)
        folder = self.project(7, "running")                       # 기록은 진행 중인데 돌고 있지 않다 = 끊긴 것
        (folder / "reviews.csv").write_text("recommendationid\n1\n2\n", encoding="utf-8-sig")
        (folder / "progress.json").write_text(json.dumps({"stage": "classify", "done": 1, "total": 2}), encoding="utf-8")
        (self.root / ".review-trash").mkdir()
        listing = main.list_games()
        self.assertEqual([g["app_id"] for g in listing["games"]], [1])
        self.assertEqual(len(listing["unfinished"]), 1)
        run = listing["unfinished"][0]
        self.assertEqual((run["app_id"], run["name"], run["status"]), (7, "멈춘 게임", "stopped"))
        self.assertIn("꺼지면서", run["error"])
        self.assertEqual(run["kept"]["collected"], 2)
        self.assertEqual(run["progress"]["total"], 2)
        self.assertEqual(run["request"]["sort"], "random")       # "이어서 하기"가 이 설정으로 다시 시작한다

    def test_run_in_progress_is_listed_as_running(self):
        self.project(7, "running")
        with patch.dict(pipeline.RUNNING, {"app_id": 7}):
            self.assertEqual(main.list_games()["unfinished"][0]["status"], "running")


class StallTests(unittest.TestCase):
    """AI가 답을 못 주는데 한 건씩 계속 묻는 일이 없어야 한다 (48시간 헛도는 것을 막는다)."""

    def test_free_models_get_a_thinking_cap(self):
        with patch.object(limits.cfg, "MODEL", "a/b:free", create=True), patch.object(limits.cfg, "MODEL_FREE", True, create=True):
            self.assertEqual(limits.thinking_fields(), {"reasoning": {"max_tokens": limits.THINKING_CAP}})
        with patch.object(limits.cfg, "MODEL", "a/paid", create=True), patch.object(limits.cfg, "MODEL_FREE", False, create=True):
            self.assertEqual(limits.thinking_fields(), {})

    def test_busy_model_stops_instead_of_asking_one_by_one(self):
        self.assertTrue(issubclass(analyzer.ModelBusy, analyzer.FatalApiError))

    def test_classification_stops_after_batches_with_no_answers(self):
        rows = [{"recommendationid": str(i), "voted_up": "1", "content": f"리뷰 본문 {i}"} for i in range(200)]
        calls = []

        async def empty_answer(client, stage, system, user, max_tokens):
            calls.append(max_tokens)
            raise ValueError("빈 답")

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(analyzer, "path", lambda name: str(Path(folder) / name)), \
                patch.object(analyzer, "ask", empty_answer), \
                patch.object(analyzer.progress, "report", lambda *a, **k: None), \
                patch.object(analyzer.limits, "concurrency", lambda paid=3: 1):
            with self.assertRaises(analyzer.FatalApiError) as stopped:
                asyncio.run(analyzer.classify(None, rows, [{"name": "기타", "desc": ""}]))
        self.assertIn("연달아", str(stopped.exception))
        self.assertEqual(len(calls), analyzer.STALL_LIMIT * (1 + analyzer.BATCH_B))   # 세 묶음만 시도하고 멈춘다


if __name__ == "__main__":
    unittest.main()
