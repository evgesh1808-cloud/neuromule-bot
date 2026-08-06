"""HTTP-клиент для OpenRouter: опциональный прокси из ``AI_PROXY``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import Settings
from platforms.telegram_proxy import (
    _normalize_proxy_url,
    _probe_proxy_reachable,
    _redact_proxy_url,
)

logger = logging.getLogger(__name__)

_OPENROUTER_PROBE_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_CONNECT_RETRIES = 5
_OPENROUTER_CONNECT_RETRY_SEC = 5.0

_proxy_logged = False
_shared_client: httpx.AsyncClient | None = None


def resolve_ai_proxy_url(settings: Settings) -> str | None:
    """Прокси для OpenRouter: ``AI_PROXY`` из ``.env``. Пусто — прямое подключение."""
    raw = (getattr(settings, "ai_proxy", None) or "").strip()
    if not raw:
        return None
    url = _normalize_proxy_url(raw)
    return url or None


def log_openrouter_proxy_configuration(settings: Settings) -> None:
    """Логирует конфигурацию прокси ровно один раз при старте приложения."""
    global _proxy_logged
    if _proxy_logged:
        return
    proxy = resolve_ai_proxy_url(settings)
    if proxy:
        logger.info("OpenRouter proxy: AI_PROXY → %s", _redact_proxy_url(proxy))
    else:
        logger.info("OpenRouter proxy: прямое подключение (AI_PROXY пуст)")
    _proxy_logged = True


def probe_openrouter_proxy(settings: Settings) -> None:
    """При старте проверяет доступность хоста/порта из ``AI_PROXY``.

    Недоступный прокси не блокирует polling — иначе бот «молчит» на все команды,
    включая /start, хотя Telegram API жив.
    """
    proxy = resolve_ai_proxy_url(settings)
    if not proxy:
        return
    try:
        if not _probe_proxy_reachable(proxy):
            logger.warning(
                "OpenRouter proxy недоступен: %s — polling стартует; "
                "проверьте AI_PROXY в .env (на VDSina нужен рабочий удалённый прокси)",
                _redact_proxy_url(proxy),
            )
            return
        logger.info("OpenRouter proxy probe OK: %s", _redact_proxy_url(proxy))
    except OSError as exc:
        logger.warning(
            "OpenRouter proxy probe failed: %s — polling стартует (%s)",
            _redact_proxy_url(proxy),
            exc,
        )


def openrouter_client_kwargs(settings: Settings, **extra: Any) -> dict[str, Any]:
    """Kwargs для ``httpx.AsyncClient`` с учётом ``AI_PROXY``."""
    kw: dict[str, Any] = dict(extra)
    kw["trust_env"] = False
    proxy = resolve_ai_proxy_url(settings)
    if proxy:
        kw["proxy"] = proxy
    return kw


async def init_openrouter_http_client(settings: Settings) -> httpx.AsyncClient:
    """Создаёт единый переиспользуемый ``AsyncClient`` для всех запросов OpenRouter."""
    global _shared_client
    if _shared_client is not None:
        return _shared_client
    _shared_client = httpx.AsyncClient(**openrouter_client_kwargs(settings))
    return _shared_client


async def get_openrouter_http_client(settings: Settings) -> httpx.AsyncClient:
    """Возвращает singleton-клиент (ленивая инициализация для тестов и tools)."""
    if _shared_client is None:
        return await init_openrouter_http_client(settings)
    return _shared_client


async def close_openrouter_http_client() -> None:
    """Закрывает singleton-клиент (shutdown бота / тестовый teardown)."""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


def reset_openrouter_startup_state_for_tests() -> None:
    """Сбрасывает флаг одноразового лога прокси (только для тестов)."""
    global _proxy_logged
    _proxy_logged = False


async def _wait_openrouter_api(settings: Settings) -> None:
    """Smoke-check OpenRouter перед polling (аналог ``_wait_telegram_api``)."""
    from services.billing.chat_pipeline import resolve_openrouter_api_key

    # Probe всегда на primary (не крутим пул FREE-ключей).
    api_key = resolve_openrouter_api_key(settings, rotate=False)
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY / OPENROUTER_API_KEYS не заданы — бот не запущен."
        )

    try:
        client = await get_openrouter_http_client(settings)
    except httpx.HTTPError as exc:
        logger.warning(
            "OpenRouter HTTP client init failed — polling стартует: %s",
            exc,
        )
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    last_error: Exception | None = None
    last_status: int | None = None

    for attempt in range(1, _OPENROUTER_CONNECT_RETRIES + 1):
        try:
            response = await client.get(
                _OPENROUTER_PROBE_URL,
                headers=headers,
                timeout=15.0,
            )
            last_status = response.status_code
            if response.status_code == 403:
                logger.warning(
                    "OpenRouter API probe HTTP 403 (вероятно Cloudflare) — "
                    "polling стартует; задайте AI_PROXY в .env для генераций"
                )
                return
            if response.status_code in (200, 401):
                logger.info("OpenRouter API OK: probe status=%s", response.status_code)
                return
            if response.status_code == 429:
                logger.warning("OpenRouter API rate limited on probe (429) — продолжаем старт")
                return
            logger.warning(
                "OpenRouter probe unexpected status=%s (attempt %s/%s)",
                response.status_code,
                attempt,
                _OPENROUTER_CONNECT_RETRIES,
            )
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning(
                "OpenRouter API недоступен (попытка %s/%s): %s",
                attempt,
                _OPENROUTER_CONNECT_RETRIES,
                exc,
            )
        if attempt < _OPENROUTER_CONNECT_RETRIES:
            await asyncio.sleep(_OPENROUTER_CONNECT_RETRY_SEC)

    proxy = resolve_ai_proxy_url(settings)
    proxy_hint = (
        f" Прокси AI_PROXY={_redact_proxy_url(proxy)} задан, но OpenRouter недоступен."
        if proxy
        else (
            " С этого хоста не открывается openrouter.ai (часто Cloudflare на VDSina). "
            "Добавьте в .env: AI_PROXY=http://user:pass@proxy-host:port"
        )
    )
    status_hint = f" last_status={last_status}" if last_status is not None else ""
    logger.warning(
        "OpenRouter API probe failed after %s attempts — polling стартует; "
        "нейротекст/картинки могут падать до восстановления связи.%s%s",
        _OPENROUTER_CONNECT_RETRIES,
        proxy_hint,
        status_hint,
        exc_info=last_error is not None,
    )
