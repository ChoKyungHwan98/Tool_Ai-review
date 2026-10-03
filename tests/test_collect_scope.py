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


if __name__ == "__main__":
    unittest.main()
