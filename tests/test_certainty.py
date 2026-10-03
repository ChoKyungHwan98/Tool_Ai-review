import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
class IntervalTests(unittest.TestCase):
    def test_wilson_range_matches_known_values(self):
        from sampling import wilson_interval as interval90
        self.assertEqual(interval90(41, 60), [57.8, 77.2])
        self.assertEqual(interval90(20, 20), [88.1, 100.0])
        self.assertEqual(interval90(15, 25), [43.7, 74.4])   # 50%를 걸친다
        self.assertIsNone(interval90(0, 0))


class PromiseTests(unittest.TestCase):
    def design(self, sort, pool=None):
        return {"population": {"total": 24537}, "design": {"n_total": 2188},
                "params": {"target_error_pct": 2.0, "sort": sort, "random_pool": pool}}

    def test_margin_matches_the_planning_formula(self):
        from sampling import cochran_n, margin_of_error
        self.assertAlmostEqual(margin_of_error(2188, 24537), 2.0, delta=.05)   # 계획할 때 쓴 값과 같아야 한다
        self.assertAlmostEqual(margin_of_error(1343, 24537), 2.6, delta=.05)
        self.assertEqual(margin_of_error(100, 100), 0.0)
        self.assertIsNone(margin_of_error(0, 100))
        # 계획한 건수는 목표 오차를 지키고, 한 건이라도 줄이면 못 지킨다
        for population in (300, 20000, 2_000_000):
            for target in (10, 5, 2.5, 1):
                n = cochran_n(population, target / 100)
                self.assertLessEqual(margin_of_error(n, population), target + 1e-9)
                if 1 < n < population:
                    self.assertGreater(margin_of_error(n - 1, population), target)

    def test_plan_keeps_the_minimum_negative_reviews(self):
        from sampling import plan_sample_size
        plan = plan_sample_size(24537, 1184, 5, 100)
        self.assertEqual((plan["n_by_error"], plan["n_for_neg"], plan["n_total"]), (379, 2073, 2073))
        self.assertTrue(plan["min_neg_driven"])
        self.assertGreaterEqual(plan["n_neg"], 100)
        self.assertEqual(plan["n_pos"] + plan["n_neg"], plan["n_total"])
        self.assertEqual(plan_sample_size(24537, 1184, 5, 100, custom=500)["n_total"], 500)
        self.assertEqual(plan_sample_size(150, 30, 5, 100)["n_total"], 150)   # 전체보다 많이 모을 수는 없다

    def test_recent_collection_never_keeps_the_promise(self):
        from dashboard_evidence import promise_check
        result = promise_check(self.design("recent"), 2188)
        self.assertFalse(result["kept"])
        self.assertEqual([c["ok"] for c in result["checks"]], [False, True, False])

    def test_random_complete_and_fully_analyzed_keeps_it(self):
        from dashboard_evidence import promise_check
        pool = {"size": 24000, "complete": True, "seed": 42}
        kept = promise_check(self.design("random", pool), 2188)
        self.assertTrue(kept["kept"])
        self.assertEqual(kept["analyzed_margin"], 2.0)   # AI 분석이 몇 건 빠져도 추천 비율의 오차는 그대로다
        self.assertFalse(promise_check(self.design("random", pool), 1500)["kept"])      # 덜 모음
        self.assertFalse(promise_check(self.design("random", {"size": 60000, "complete": False}), 2188)["kept"])
        self.assertIsNone(promise_check(self.design("recent"), 2188)["analyzed_margin"])

    def test_no_plan_means_no_check(self):
        from dashboard_evidence import promise_check
        self.assertIsNone(promise_check({}, 10))

    def test_custom_sample_does_not_inherit_the_target_margin(self):
        from dashboard_evidence import promise_check
        design = {"population": {"total": 10000}, "design": {"n_total": 5},
                  "params": {"target_error_pct": 5, "sort": "random",
                             "random_pool": {"size": 10000, "complete": True}}}
        result = promise_check(design, 5)
        self.assertFalse(result["kept"])
        self.assertFalse(result["checks"][2]["ok"])
