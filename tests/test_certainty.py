import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
class IntervalTests(unittest.TestCase):
    def test_wilson_range_matches_known_values(self):
        from dashboard_evidence import interval90
        self.assertEqual(interval90(41, 60), [57.8, 77.2])
        self.assertEqual(interval90(20, 20), [88.1, 100.0])
        self.assertEqual(interval90(15, 25), [43.7, 74.4])   # 50%를 걸친다
        self.assertIsNone(interval90(0, 0))


class PromiseTests(unittest.TestCase):
    def design(self, sort, pool=None):
        return {"population": {"total": 24537}, "design": {"n_total": 2188},
                "params": {"target_error_pct": 2.0, "sort": sort, "random_pool": pool}}

    def test_margin_matches_the_planning_formula(self):
        from dashboard_evidence import margin95
        self.assertAlmostEqual(margin95(2188, 24537), 2.0, delta=.05)   # 계획할 때 쓴 값과 같아야 한다
        self.assertAlmostEqual(margin95(1343, 24537), 2.6, delta=.05)
        self.assertEqual(margin95(100, 100), 0.0)
        self.assertIsNone(margin95(0, 100))

    def test_recent_collection_never_keeps_the_promise(self):
        from dashboard_evidence import promise_check
        result = promise_check(self.design("recent"), 2188, 2188, 2188)
        self.assertFalse(result["kept"])
        self.assertEqual([c["ok"] for c in result["checks"]], [False, True, False, True])

    def test_random_complete_and_fully_analyzed_keeps_it(self):
        from dashboard_evidence import promise_check
        pool = {"size": 24000, "complete": True, "seed": 42}
        self.assertTrue(promise_check(self.design("random", pool), 2188, 2188, 2188)["kept"])
        self.assertFalse(promise_check(self.design("random", pool), 2188, 2187, 2188)["kept"])
        self.assertFalse(promise_check(self.design("random", pool), 2188, 1343, 2188)["kept"])      # 분석이 빠짐
        self.assertFalse(promise_check(self.design("random", pool), 1500, 1500, 1500)["kept"])      # 덜 모음
        self.assertFalse(promise_check(self.design("random", {"size": 60000, "complete": False}), 2188, 2188, 2188)["kept"])
        self.assertIsNone(promise_check(self.design("recent"), 2188, 2188, 2188)["analyzed_margin"])

    def test_no_plan_means_no_check(self):
        from dashboard_evidence import promise_check
        self.assertIsNone(promise_check({}, 10, 10, 10))

    def test_custom_sample_does_not_inherit_the_target_margin(self):
        from dashboard_evidence import promise_check
        design = {"population": {"total": 10000}, "design": {"n_total": 5},
                  "params": {"target_error_pct": 5, "sort": "random",
                             "random_pool": {"size": 10000, "complete": True}}}
        result = promise_check(design, 5, 5, 5)
        self.assertFalse(result["kept"])
        self.assertFalse(result["checks"][2]["ok"])
