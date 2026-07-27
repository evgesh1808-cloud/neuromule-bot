"""Каталог живых OpenRouter ``:free`` моделей для FREE-каскада.

Раз в час опрашивает ``/api/v1/models``, фильтрует текстовые ``:free``,
ранжирует (preferred first). При сбое — аварийный резерв из chat_pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config import settings
from services.openrouter_http import get_openrouter_http_client

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# Redis-ключ для шаринга каталога между воркерами / инстансами.
REDIS_FREE_MODELS_KEY = "active_free_models_list"
FREE_MODELS_REFRESH_SEC = 3600.0
FREE_CASCADE_MAX_MODELS = 8

# Приоритет проверенным гигантам, если они ONLINE в каталоге.
_PREFERRED_FREE_MODELS: tuple[str, ...] = (
    "deepseek/deepseek-r1-distill-llama-8b:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2.5-7b-instruct:free",
)

_EMERGENCY_FREE_MODELS: tuple[str, ...] = (
    "deepseek/deepseek-r1-distill-llama-8b:free",
    "meta-llama/llama-3.1-8b-instruct:free",
)

_cache_models: list[str] = []
_cache_fetched_at: float = 0.0
_cache_lock = asyncio.Lock()


def emergency_free_models() -> list[str]:
    return list(_EMERGENCY_FREE_MODELS)


def get_cached_free_models() -> list[str]:
    """Последний успешный снимок (process-local; Redis подтягивается в refresh)."""
    return list(_cache_models)


def reset_free_models_cache_for_tests() -> None:
    global _cache_models, _cache_fetched_at
    _cache_models = []
    _cache_fetched_at = 0.0


async def _redis_load_free_models() -> list[str] | None:
    """Читает ``active_free_models_list`` из Redis (JSON-массив ID)."""
    url = (getattr(settings, "redis_url", None) or "").strip()
    if not url:
        return None
    try:
        import json

        import redis.asyncio as redis

        client = redis.from_url(url, encoding="utf-8", decode_responses=True)
        try:
            raw = await client.get(REDIS_FREE_MODELS_KEY)
        finally:
            await client.aclose()
        if not raw:
            return None
        data = json.loads(raw)
        if not isinstance(data, list):
            return None
        return [str(x).strip() for x in data if str(x).strip().endswith(":free")]
    except Exception:
        logger.debug("Redis load active_free_models_list failed", exc_info=True)
        return None


async def _redis_save_free_models(models: list[str]) -> None:
    """Пишет каталог в Redis с TTL чуть больше часа (страховка от stale)."""
    url = (getattr(settings, "redis_url", None) or "").strip()
    if not url:
        return
    try:
        import json

        import redis.asyncio as redis

        client = redis.from_url(url, encoding="utf-8", decode_responses=True)
        try:
            await client.set(
                REDIS_FREE_MODELS_KEY,
                json.dumps(models, ensure_ascii=False),
                ex=int(FREE_MODELS_REFRESH_SEC) + 300,
            )
        finally:
            await client.aclose()
    except Exception:
        logger.debug("Redis save active_free_models_list failed", exc_info=True)


def _is_text_free_model(model: dict[str, Any]) -> bool:
    mid = str(model.get("id") or "").strip()
    if not mid.endswith(":free"):
        return False
    if "context_length" not in model:
        return False
    arch = model.get("architecture") or {}
    modality = str(arch.get("modality") or "").lower()
    if modality and "text" not in modality.split("->")[0]:
        # image->image / audio и т.п.
        return False
    inputs = arch.get("input_modalities")
    if isinstance(inputs, list) and inputs and "text" not in inputs:
        return False
    return True


def rank_free_models(free_ids: list[str]) -> list[str]:
    """Preferred online first, затем остальные (без дублей)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for mid in (*_PREFERRED_FREE_MODELS, *free_ids):
        if mid in free_ids and mid not in seen:
            seen.add(mid)
            ordered.append(mid)
    return ordered


async def fetch_active_free_models(
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> list[str]:
    """
    Опрашивает OpenRouter, вытаскивает работающие текстовые ``:free`` модели.

    При ошибке/пустом ответе — жёсткий аварийный резерв (2 модели).
    """
    try:
        http = client or await get_openrouter_http_client(settings)
        headers: dict[str, str] = {}
        try:
            from services.billing.chat_pipeline import resolve_openrouter_api_key

            api_key = resolve_openrouter_api_key(settings, rotate=False)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        except Exception:
            logger.debug("free models: API key resolve skipped", exc_info=True)

        response = await http.get(
            OPENROUTER_MODELS_URL,
            headers=headers or None,
            timeout=timeout,
        )
        if response.status_code != 200:
            logger.warning(
                "fetch_active_free_models: status=%s", response.status_code
            )
            return emergency_free_models()

        data = response.json()
        free_models = [
            str(model["id"]).strip()
            for model in data.get("data", [])
            if isinstance(model, dict) and _is_text_free_model(model)
        ]
        if not free_models:
            logger.warning("fetch_active_free_models: empty :free list")
            return emergency_free_models()

        ordered = rank_free_models(free_models)
        logger.info(
            "fetch_active_free_models: online=%s cascade_head=%s",
            len(ordered),
            ordered[:4],
        )
        return ordered
    except Exception:
        logger.exception("Ошибка обновления списка бесплатных моделей")
        return emergency_free_models()


async def refresh_free_models_cache(*, force: bool = False) -> list[str]:
    """Обновляет кэш (память + Redis); не чаще 1 раза в час (если не force)."""
    global _cache_models, _cache_fetched_at
    async with _cache_lock:
        now = time.monotonic()
        if (
            not force
            and _cache_models
            and (now - _cache_fetched_at) < FREE_MODELS_REFRESH_SEC
        ):
            return list(_cache_models)

        # Сначала пробуем свежий снимок из Redis (другой инстанс мог уже обновить).
        if not force:
            from_redis = await _redis_load_free_models()
            if from_redis:
                _cache_models = list(from_redis)
                _cache_fetched_at = now
                return list(_cache_models)

        models = await fetch_active_free_models()
        _cache_models = list(models)
        _cache_fetched_at = now
        await _redis_save_free_models(_cache_models)
        return list(_cache_models)


def free_cascade_from_cache() -> tuple[str, ...]:
    """Каскад для chat_pipeline: кэш (cap); до первого fetch — preferred; пусто → emergency."""
    models = get_cached_free_models()
    if not models:
        models = list(_PREFERRED_FREE_MODELS)
    cleaned = [m for m in models if str(m).endswith(":free")]
    if not cleaned:
        cleaned = emergency_free_models()
    return tuple(cleaned[:FREE_CASCADE_MAX_MODELS])


async def free_models_refresh_loop(
    interval_sec: float = FREE_MODELS_REFRESH_SEC,
) -> None:
    """Фон: первый fetch сразу, далее раз в ``interval_sec``."""
    while True:
        try:
            await refresh_free_models_cache(force=True)
        except Exception:
            logger.exception("free_models_refresh_loop tick failed")
        await asyncio.sleep(max(60.0, float(interval_sec)))
