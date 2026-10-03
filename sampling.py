"""표본 수식. 수집 계획, 분석 시작 창, 분석 방법 화면이 모두 여기 한 곳의 식을 쓴다.

파일·네트워크·설정을 읽지 않는 순수 계산만 둔다.
"""
import math

Z_95 = 1.96
Z_90 = 1.645
# 분석 시작 창의 손잡이 눈금(목표 오차 ±%). static/dashboard.html의 PRECISION_STOPS와 같아야 한다.
PRECISION_STOPS = (10, 8, 7, 6, 5, 4, 3, 2.5, 2, 1.5, 1)


def cochran_n(population, error, p=0.5, z=Z_95):
    """비율 하나를 ±error(0~1) 안에서 말하려면 무작위로 뽑아야 하는 건수 (유한모집단 보정)."""
    n0 = (z ** 2 * p * (1 - p)) / (error ** 2)
    return math.ceil(n0 / (1 + (n0 - 1) / population)) if population > 0 else math.ceil(n0)


def margin_of_error(n, population, p=0.5, z=Z_95):
    """n건을 무작위로 뽑았을 때 비율 하나의 최대 오차(%p). 반올림하지 않는다."""
    if not n or not population or n <= 0 or population <= 0:
        return None
    if n >= population:
        return 0.0
    return z * math.sqrt((p * (1 - p) / n) * (population - n) / (population - 1)) * 100


def plan_sample_size(total, negative, target_error_pct, min_neg, custom=None):
    """수집할 건수를 정한다. 목표 오차와 비추천 최소 건수 중 더 많이 필요한 쪽을 따른다.

    custom이 있으면 그 건수를 그대로 쓴다. 추천·비추천 건수는 전체 비율대로 나눈다.
    """
    neg_rate = negative / total if total else 0
    by_error = cochran_n(total, target_error_pct / 100)
    for_neg = math.ceil(min_neg / neg_rate) if neg_rate > 0 else by_error
    n = min(custom if custom else max(by_error, for_neg), total)
    n_pos = math.ceil(n * (1 - neg_rate))
    return {"n_total": n, "n_pos": n_pos, "n_neg": n - n_pos,
            "n_by_error": by_error, "n_for_neg": for_neg,
            "min_neg_driven": not custom and for_neg > by_error}


def wilson_interval(hits, n, z=Z_90):
    """비율 hits/n의 Wilson 범위 [낮은 %, 높은 %]. 건수가 적어 생기는 흔들림만 잰다."""
    if not n:
        return None
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half) * 100, 1), round(min(1.0, centre + half) * 100, 1)]
