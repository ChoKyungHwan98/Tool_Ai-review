"""OpenRouter 무료 모델(:free) 한도에 맞춰 요청 속도를 조절한다.

OpenRouter 무료 모델 한도: 분당 20회. 하루 50회, 평생 10달러 이상 충전한 계정은 하루 1,000회.
실패한 429 요청도 하루 한도에 포함되므로 무작정 재시도하지 않는다.
유료 모델은 기다리지 않는다.
"""
import asyncio
import os
import threading
import time

from config import cfg

FREE_RPM = 20
FREE_DAILY = int(os.getenv("FREE_DAILY_REQUESTS", "1000"))   # 10달러 이상 충전한 계정 기준
GAP = 60 / (FREE_RPM - 2)                                    # 여유를 두고 약 3.3초에 한 번
DAILY_MESSAGE = ("OpenRouter 무료 모델의 하루 요청 한도에 도달했습니다. "
                 "내일 같은 설정으로 다시 실행하면 이미 분석한 리뷰는 건너뛰고 이어서 분석합니다.")

_lock = threading.Lock()
_next = [0.0]


def is_free():
    return bool(getattr(cfg, "MODEL_FREE", False)) or str(getattr(cfg, "MODEL", "")).endswith(":free")


def concurrency(paid=3):
    return 1 if is_free() else paid


def _delay():
    if not is_free():
        return 0.0
    with _lock:
        now = time.monotonic()
        start = max(now, _next[0])
        _next[0] = start + GAP
        return start - now


def wait_turn():
    time.sleep(_delay())


async def await_turn():
    await asyncio.sleep(_delay())


def model_fields():
    """요청에 넣을 모델 지정. 무료 모델은 붐빌 때 대신 답할 모델을 같이 보낸다(OpenRouter의 models 배열)."""
    fallbacks = getattr(cfg, "MODEL_FALLBACKS", None) or []
    return {"models": [cfg.MODEL, *fallbacks]} if fallbacks and is_free() else {"model": cfg.MODEL}


THINKING_CAP = 400


def thinking_fields():
    """'생각'에 쓸 토큰 상한. 대신 답하는 무료 모델 중에는 답 길이 한도를 생각에 다 써 버려 빈 답을 주는 모델이 있다
    (묶음마다 40초씩 쓰고 한 건도 분류하지 못한다). 상한을 두면 같은 묶음을 몇 초 만에 답한다. 생각하지 않는 모델은 이 값을 무시한다."""
    return {"reasoning": {"max_tokens": THINKING_CAP}} if is_free() else {}


def answer_room(max_tokens):
    """답 길이 한도. 무료 모델 중에는 답을 쓰기 전에 '생각'에 토큰을 쓰는 모델이 있어, 한도가 빠듯하면 답이 비어서 온다.
    무료 모델은 비용이 없으므로 네 배로 넉넉히 준다."""
    return max_tokens * 4 if is_free() else max_tokens


def daily_limit_hit(text):
    text = (text or "").lower()
    return "per-day" in text or "per day" in text or "free-models-per-day" in text


def retry_after(response, default=10.0):
    """429 응답이 알려준 대기 시간(초). 없으면 기본값."""
    try:
        return min(60.0, max(1.0, float(response.headers.get("retry-after"))))
    except (TypeError, ValueError):
        return default
