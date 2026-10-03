import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_reviews_v3 as analyzer


def reviews(n_up, n_down):
    rows = [{"recommendationid": f"u{i}", "voted_up": "1", "content": f"추천하는 리뷰 본문 {i:04d} 충분히 긴 글입니다"} for i in range(n_up)]
    rows += [{"recommendationid": f"d{i}", "voted_up": "0", "content": f"비추천하는 리뷰 본문 {i:04d} 충분히 긴 글입니다"} for i in range(n_down)]
    return rows


class ThemeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_samples_do_not_share_reviews_and_carry_both_votes(self):
        samples = analyzer.theme_samples(reviews(600, 60))
        self.assertEqual(len(samples), analyzer.THEME_SAMPLES)
        texts = [item["t"] for sample in samples for item in sample]
        self.assertEqual(len(texts), len(set(texts)))                       # 묶음끼리 겹치지 않는다
        for sample in samples:
            self.assertLessEqual(len(sample), analyzer.THEME_SAMPLE)
            self.assertEqual(sum(item["up"] == 0 for item in sample), 20)   # 비추천 60건을 셋으로 나눠 담는다
        self.assertEqual(len(analyzer.theme_samples(reviews(50, 5))), 1)    # 리뷰가 적으면 한 묶음

    def test_feelings_and_duplicates_are_dropped(self):
        themes = analyzer.clean_themes([
            {"name": "저장", "desc": "세이브"}, {"name": "시간 순삭", "desc": ""}, {"name": "몰입감", "desc": ""},
            {"name": "저 장", "desc": "띄어쓰기만 다름"}, {"name": "", "desc": ""}, {"name": "기타", "desc": ""}, "잘못된 형식"])
        self.assertEqual([t["name"] for t in themes], ["저장"])
        self.assertEqual(themes[0]["v"], analyzer.THEME_VERSION)

    def test_candidates_from_every_sample_are_consolidated_once(self):
        answers = [
            [{"name": "저장", "desc": "저장 오류"}, {"name": "시간", "desc": "시간 순삭"}, {"name": "최적화", "desc": "성능"}],
            [{"name": "데이터 삭제", "desc": "세이브 날아감"}, {"name": "최적화", "desc": "렉"}],
            [{"name": "최적화", "desc": "프레임"}, {"name": "전투", "desc": "싸움"}],
            [{"name": "저장", "desc": "저장 오류와 데이터 삭제"}, {"name": "최적화", "desc": "성능과 렉"}, {"name": "전투", "desc": "싸움"}],
        ]
        with patch.object(analyzer, "path", side_effect=lambda name: str(self.folder / name)), \
             patch.object(analyzer, "ask", new=AsyncMock(side_effect=answers)) as ask:
            themes = asyncio.run(analyzer.find_themes(None, reviews(600, 60)))
        self.assertEqual(ask.await_count, 4)                                 # 묶음 셋 + 정리 한 번
        sent = ask.await_args_list[3].args[3]
        self.assertNotIn('"시간"', sent)                                     # 느낌 후보는 정리 단계에 보내지도 않는다
        self.assertIn('"name":"최적화","desc":"성능","seen":3', sent)        # 세 묶음 모두에서 나온 후보
        self.assertEqual([t["name"] for t in themes], ["저장", "최적화", "전투", "기타"])
        self.assertTrue(analyzer.themes_are_current(json.loads((self.folder / "themes_v3.json").read_text(encoding="utf-8"))))

    def test_themes_made_the_old_way_are_found_again(self):
        (self.folder / "themes_v3.json").write_text(json.dumps([{"name": "시간", "desc": "", "area": "gameplay"}]), encoding="utf-8")
        answers = [[{"name": "전투", "desc": "싸움"}]] * 2
        with patch.object(analyzer, "path", side_effect=lambda name: str(self.folder / name)), \
             patch.object(analyzer, "ask", new=AsyncMock(side_effect=answers)):
            themes = asyncio.run(analyzer.find_themes(None, reviews(50, 5)))
        self.assertEqual([t["name"] for t in themes], ["전투", "기타"])
        self.assertFalse(analyzer.themes_are_current([{"name": "시간"}]))

    def test_classifier_sees_descriptions_but_stores_names(self):
        themes = [{"name": "최적화", "desc": "게임 성능과 끊김"}, {"name": "기타"}]
        self.assertEqual(analyzer.theme_guide(themes), "최적화: 게임 성능과 끊김; 기타")
        tags = analyzer.clean_tags([["최적화: 게임 성능과 끊김", "N"], ["최적화", "N"], ["없는 주제", "P"], ["기타", "X"]], ["최적화", "기타"])
        self.assertEqual(tags, [["최적화", "N"]])


if __name__ == "__main__":
    unittest.main()
