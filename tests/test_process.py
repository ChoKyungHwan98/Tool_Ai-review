import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_reviews_v3 as analyzer
from dashboard_evidence import is_request


class AnswerParsingTests(unittest.TestCase):
    """모델마다 답의 모양이 다르다. 읽을 수 있는 답을 형식 때문에 버리면 그 리뷰가 분석에서 빠진다."""

    def test_reads_the_shapes_models_actually_return(self):
        cases = {
            '[{"id":"1"},{"id":"2"}]': 2,
            '{"items":[{"id":"1"}]}': 1,
            '```json\n[{"id":"1"}]\n```': 1,
            '[{"id":"1"},{"id":"2"}]\n]': 2,                                   # 괄호를 하나 더 붙임
            '[{"id":"1","t":[["a","P"]]}]\n{"id":"2","t":[]}\n{"id":"3"}': 3,   # 첫 항목만 배열, 나머지는 줄마다
            '예시는 {"id":"x"} 처럼.\n{"items":[{"id":"1"},{"id":"2"},{"id":"3"}]}': 3,   # 설명 속 예시는 버린다
            '{"id":"1"},{"id":"2"}': 2,
        }
        for text, count in cases.items():
            self.assertEqual(len(analyzer.parse_answer(text)), count, text)

    def test_text_without_an_answer_is_an_error(self):
        with self.assertRaises(ValueError):
            analyzer.parse_answer("답을 드릴 수 없습니다")


class RequestTests(unittest.TestCase):
    """'바라는 것'에는 무엇을 바라는지 알 수 있는 말만 올린다."""

    def test_theme_name_plus_improve_is_not_a_request(self):
        for vague in ("저장 기능 개선", "서버 개선", "최적화 개선", "패치좀 해라 제발", "제발 고쳐주세요."):
            self.assertFalse(is_request(vague, "저장"), vague)
        self.assertFalse(is_request("서버 관리 개선", "서버"))

    def test_concrete_requests_are_kept(self):
        for concrete in ("저장 백업 기능 추가", "자동 저장 추가해주세요", "카메라 거리 조절 옵션 추가", "팰 디자인을 수정해줬으면 좋겠다"):
            self.assertTrue(is_request(concrete, "저장"), concrete)


if __name__ == "__main__":
    unittest.main()
