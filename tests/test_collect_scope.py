import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect_reviews as collector
from config import cfg


def page(ts_list, cursor):
    response = MagicMock()
    response.json.return_value = {
        "cursor": cursor,
        "reviews": [{"recommendationid": str(ts), "review": "리뷰", "voted_up": True,
                     "timestamp_created": ts, "author": {}} for ts in ts_list],
    }
    return response


class CollectScopeTests(unittest.TestCase):
    def setUp(self):
        self.saved = (cfg.COLLECT_SINCE, cfg.COLLECT_SORT)

    def tearDown(self):
        cfg.COLLECT_SINCE, cfg.COLLECT_SORT = self.saved

    def test_recent_reviews_stop_at_the_chosen_date(self):
        cfg.COLLECT_SINCE, cfg.COLLECT_SORT = "2026-09-01", "recent"
        cut = collector.get_since_ts()
        pages = [page([cut + 20, cut + 10], "a"), page([cut + 5, cut - 5], "b"), page([cut - 10, cut - 20], "c")]
        with patch.object(collector.httpx, "get", side_effect=pages) as get, patch.object(collector.time, "sleep"):
            rows = collector.collect_reviews("positive", 100, set())
        self.assertEqual([r["timestamp_created"] for r in rows], [cut + 20, cut + 10, cut + 5])
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args.kwargs["params"]["filter"], "recent")

    def test_helpful_sort_uses_steam_all_filter_with_day_range(self):
        cfg.COLLECT_SINCE, cfg.COLLECT_SORT = None, "helpful"
        with patch.object(collector.httpx, "get", side_effect=[page([], "a")]) as get:
            collector.collect_reviews("negative", 10, set())
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["filter"], "all")
        self.assertNotIn("day_range", params)

    def test_random_pool_is_complete_only_after_reaching_the_scope_end(self):
        cfg.COLLECT_SINCE, cfg.COLLECT_SORT = None, "random"
        status = {"complete": False}
        with patch.object(collector.httpx, "get", side_effect=[page([1, 2], "a")]), patch.object(collector.time, "sleep"):
            collector.collect_reviews("all", 2, set(), scan_status=status)
        self.assertFalse(status["complete"])

        status = {"complete": False}
        with patch.object(collector.httpx, "get", side_effect=[page([1, 2], "a"), page([], "b")]), patch.object(collector.time, "sleep"):
            collector.collect_reviews("all", 3, set(), scan_status=status)
        self.assertTrue(status["complete"])

    def test_rate_limited_page_is_asked_again_after_waiting(self):
        cfg.COLLECT_SINCE, cfg.COLLECT_SORT = None, "random"
        busy = MagicMock(status_code=429)
        with patch.object(collector.httpx, "get", side_effect=[page([1, 2], "a"), busy, busy, page([3], "b"), page([], "c")]) as get, \
             patch.object(collector.time, "sleep") as sleep:
            rows = collector.collect_reviews("all", 10, set())
        self.assertEqual([r["recommendationid"] for r in rows], ["1", "2", "3"])   # 거절당한 쪽을 건너뛰지 않는다
        self.assertEqual(get.call_args_list[1].kwargs["params"]["cursor"], get.call_args_list[3].kwargs["params"]["cursor"])
        self.assertIn(30, [c.args[0] for c in sleep.call_args_list])
        self.assertIn(60, [c.args[0] for c in sleep.call_args_list])

    def test_endless_rate_limit_stops_with_a_plain_message(self):
        cfg.COLLECT_SINCE, cfg.COLLECT_SORT = None, "random"
        with patch.object(collector.httpx, "get", return_value=MagicMock(status_code=429)), patch.object(collector.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "10분쯤 뒤"):
                collector.collect_reviews("all", 10, set())

    def test_interrupted_scan_resumes_from_the_saved_page(self):
        import tempfile
        cfg.COLLECT_SINCE, cfg.COLLECT_SORT = None, "random"
        scope = {"app_id": 1, "language": "koreana", "since": None}
        with tempfile.TemporaryDirectory() as folder:
            first = collector.ScanCheckpoint(folder, scope)
            busy = MagicMock(status_code=429)
            with patch.object(collector.httpx, "get", side_effect=[page([1, 2], "a"), page([3, 4], "b")] + [busy] * 6), \
                 patch.object(collector.time, "sleep"):
                with self.assertRaisesRegex(ValueError, "이어서"):
                    collector.collect_reviews("all", 100, set(), checkpoint=first)

            second = collector.ScanCheckpoint(folder, scope)
            self.assertTrue(second.load())
            self.assertEqual((len(second.rows), second.cursor), (4, "b"))
            with patch.object(collector.httpx, "get", side_effect=[page([5], "c"), page([], "d")]) as get, \
                 patch.object(collector.time, "sleep"):
                rows = collector.collect_reviews("all", 100, set(), checkpoint=second)
            self.assertEqual([r["recommendationid"] for r in rows], ["1", "2", "3", "4", "5"])   # 순서도 그대로
            self.assertEqual(get.call_args_list[0].kwargs["params"]["cursor"], "b")              # 멈춘 쪽부터 다시 묻는다

            other = collector.ScanCheckpoint(folder, {**scope, "since": "2026-09-01"})
            self.assertFalse(other.load())                                                       # 범위가 다르면 쓰지 않는다
            second.clear()
            self.assertFalse(collector.ScanCheckpoint(folder, scope).load())


if __name__ == "__main__":
    unittest.main()
