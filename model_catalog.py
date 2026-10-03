"""Live OpenRouter text-model catalog and per-token prices.

No invented fallback prices: when the catalog is unavailable, a new run waits.
"""
import math
import threading
import time

import httpx


URL = "https://openrouter.ai/api/v1/models"
TTL_SECONDS = 15 * 60
_lock = threading.Lock()
_cached = []
_fetched_at = 0.0


def _price(value):
    try:
        result = float(value) * 1_000_000
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def list_models(force=False):
    global _cached, _fetched_at
    with _lock:
        if _cached and not force and time.monotonic() - _fetched_at < TTL_SECONDS:
            return list(_cached)
    response = httpx.get(URL, timeout=15.0)
    response.raise_for_status()
    models = []
    for raw in response.json().get("data", []):
        architecture = raw.get("architecture") or {}
        inputs = architecture.get("input_modalities") or []
        outputs = architecture.get("output_modalities") or []
        if "text" not in inputs or "text" not in outputs:
            continue
        pricing = raw.get("pricing") or {}
        prompt = _price(pricing.get("prompt"))
        completion = _price(pricing.get("completion"))
        request = _price(pricing.get("request", 0))
        if prompt is None or completion is None or request is None:
            continue
        model_id = raw.get("id")
        if not model_id:
            continue
        context_length = int(raw.get("context_length") or 0)
        if context_length < 32768:
            continue
        params = raw.get("supported_parameters") or []
        models.append({
            "id": model_id, "name": raw.get("name") or model_id,
            "input_cost": prompt, "output_cost": completion,
            "request_cost": request, "free": prompt == completion == request == 0,
            "context_length": context_length,
            "json_schema": "response_format" in params,
        })
    if not models:
        raise ValueError("사용 가능한 텍스트 분석 모델을 찾지 못했습니다")
    models.sort(key=lambda m: (not m["free"], m["input_cost"] + m["output_cost"], m["name"].casefold()))
    with _lock:
        _cached, _fetched_at = models, time.monotonic()
    return list(models)


SMALL_WORDS = ("nano", "mini", "small", "tiny", "lite", "reasoning", "preview")
MIN_FALLBACK_SIZE = 20   # 이름에 적힌 크기(○○b)가 이보다 작은 모델은 긴 JSON 답을 끝까지 못 쓰는 일이 잦다


def model_size(model_id):
    """모델 이름에 적힌 크기(예: '-31b-' → 31). 적혀 있지 않으면 0."""
    import re
    sizes = [float(x) for x in re.findall(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z0-9])", model_id.lower())]
    return max(sizes, default=0)


def free_fallbacks(model_id):
    """고른 무료 모델이 붐빌 때 대신 답할 무료 모델(최대 2개). 같은 회사 모델을 먼저, 그다음 크기가 큰 순서.

    무료 모델은 여러 사람이 같이 써서 수시로 요청을 거절한다. 대체 모델이 없으면 분석이 그때마다 멈춘다.
    OpenRouter의 무료 라우터(openrouter/free)는 쓰지 않는다. 아주 작은 모델로 보내는 일이 있어 JSON 답이 중간에 끊긴다.
    """
    vendor = model_id.split("/")[0]
    usable = [m for m in list_models()
              if m["free"] and m["id"].endswith(":free") and m["id"] != model_id and m.get("json_schema")
              and model_size(m["id"]) >= MIN_FALLBACK_SIZE and not any(word in m["id"].lower() for word in SMALL_WORDS)]
    usable.sort(key=lambda m: (m["id"].split("/")[0] != vendor, -model_size(m["id"])))
    return [m["id"] for m in usable[:2]]


def get_model(model_id):
    return next((model for model in list_models() if model["id"] == model_id), None)
