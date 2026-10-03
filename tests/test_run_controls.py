import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import model_catalog
from budget_control import BudgetExceeded, BudgetGuard


class RunControlsTests(unittest.TestCase):
    def test_model_catalog_classifies_live_free_and_paid_prices(self):
        payload = {"data": [
            {"id": "a/free", "name": "무료", "context_length": 32768,
             "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
             "pricing": {"prompt": "0", "completion": "0", "request": "0"}, "supported_parameters": []},
            {"id": "b/paid", "name": "유료", "context_length": 32768,
             "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
             "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0"},
             "supported_parameters": ["response_format"]},
            {"id": "c/image", "architecture": {"input_modalities": ["image"], "output_modalities": ["image"]},
             "pricing": {"prompt": "0", "completion": "0"}},
        ]}
        with patch.object(model_catalog.httpx, "get", return_value=Mock(json=lambda: payload, raise_for_status=lambda: None)):
            models = model_catalog.list_models(force=True)
        self.assertEqual([item["id"] for item in models], ["a/free", "b/paid"])
        self.assertTrue(models[0]["free"])
        self.assertEqual((models[1]["input_cost"], models[1]["output_cost"]), (1.0, 2.0))
        self.assertTrue(models[1]["json_schema"])

    def test_budget_guard_reserves_parallel_calls_and_stops_before_dispatch(self):
        guard = BudgetGuard(limit_usd=0.003, input_per_million=1, output_per_million=1)
        messages = [{"role": "user", "content": "짧은 리뷰"}]
        reservation = guard.reserve(messages, 1000)
        with self.assertRaises(BudgetExceeded):
            guard.reserve(messages, 2000)
        guard.finish(reservation, {"prompt_tokens": 20, "completion_tokens": 10})
        self.assertLess(guard.spent, 0.001)
        self.assertGreater(guard.reserve(messages, 1000), 0)

    def test_library_hides_unfinished_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            complete = root / "1"
            complete.mkdir()
            (complete / "insights_v5.json").write_text("{}", encoding="utf-8")
            with patch.object(main.cfg, "PROJECTS_DIR", str(root)), \
                 patch.object(main, "_load_games", return_value=[{"app_id": 1}, {"app_id": 2}]):
                self.assertEqual(main.list_games(), {"games": [{"app_id": 1}]})

    def test_new_project_published_only_after_successful_insights(self):
        class DeferredTasks:
            task = None
            def add_task(self, callback):
                self.task = callback

        tasks = DeferredTasks()
        request = main.PipelineRunRequest(app_id=999, lang="english", model="a/free")
        with patch("model_catalog.get_model", return_value={"id": "a/free"}), \
             patch.object(main, "run_pipeline", return_value={"status": "failed"}) as run, \
             patch.object(main, "_load_games", return_value=[]), \
             patch.object(main, "_save_games") as save, \
             patch.object(main, "search_steam_game", return_value={"app_id": 999, "name": "테스트 게임"}):
            self.assertEqual(main.trigger_pipeline(request, tasks)["status"], "started")
            save.assert_not_called()
            tasks.task()
            save.assert_not_called()
            run.return_value = {"status": "done", "steps": {"insights": {"status": "done"}}}
            tasks.task()
            save.assert_called_once_with([{"app_id": 999, "name": "테스트 게임"}])

    def test_language_stats_use_selected_population(self):
        answers = iter([
            {"query_summary": {"total_reviews": 200, "total_positive": 100, "total_negative": 100}},
            {"query_summary": {"total_reviews": 1000, "total_positive": 900, "total_negative": 100,
                               "review_score_desc": "Very Positive"}},
        ])
        def fake_get(_url, params, timeout):
            response = Mock()
            response.json.return_value = next(answers)
            response.raise_for_status.return_value = None
            requested.append(params["language"])
            return response
        requested = []
        with patch("httpx.get", side_effect=fake_get):
            stats = main.review_population_stats(1, "english")
        self.assertEqual(requested, ["english", "all"])
        self.assertEqual(stats["selected"]["neg_rate"], 50)
        self.assertIsNone(stats["korean"])

    def test_sample_preview_uses_the_collectors_conservative_formula(self):
        answers = iter([
            {"query_summary": {"total_reviews": 10000, "total_positive": 7000, "total_negative": 3000}},
            {"query_summary": {"total_reviews": 10000, "total_positive": 7000, "total_negative": 3000}},
        ])
        def fake_get(_url, params, timeout):
            response = Mock()
            response.json.return_value = next(answers)
            response.raise_for_status.return_value = None
            return response
        with patch("httpx.get", side_effect=fake_get):
            stats = main.review_population_stats(1, "english")
        self.assertEqual(stats["sample_design"]["p_applied"], 0.5)
        self.assertEqual(stats["sample_design"]["cochran_5pct"], 370)
        self.assertEqual(stats["sample_design"]["sample_5pct"], 370)


if __name__ == "__main__":
    unittest.main()
