"""Повторные попытки VK API при flood-control (error_code 6 / 9 / 29)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_VK_FLOOD_ERROR_CODES = frozenset({6, 9, 29})
_DEFAULT_MAX_ATTEMPTS = 5
_DEFAULT_BASE_DELAY_SEC = 0.5
_DEFAULT_MAX_DELAY_SEC = 30.0

_T = TypeVar("_T")


def _extract_vk_error_code(exc: BaseException) -> int | None:
    code = getattr(exc, "error_code", None)
    if isinstance(code, int):
        return code
    for attr in ("code", "error_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _response_has_flood_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    err = payload.get("error")
    if not isinstance(err, dict):
        return False
    code = err.get("error_code")
    return isinstance(code, int) and code in _VK_FLOOD_ERROR_CODES


def is_vk_flood_error(exc: BaseException) -> bool:
    code = _extract_vk_error_code(exc)
    if code in _VK_FLOOD_ERROR_CODES:
        return True
    text = str(exc).lower()
    return "too many requests" in text or "flood" in text


async def vk_api_call_with_retry(
    call: Callable[[], Awaitable[_T]],
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_sec: float = _DEFAULT_BASE_DELAY_SEC,
    max_delay_sec: float = _DEFAULT_MAX_DELAY_SEC,
    context: str = "vk_api",
) -> _T:
    """
    Вызывает ``call()`` с экспоненциальной задержкой при flood-control VK.

    Поддерживает исключения vkbottle/VK API и dict-ответы с полем ``error``.
    """
    delay = base_delay_sec
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = await call()
            if _response_has_flood_error(result):
                if attempt >= max_attempts:
                    raise RuntimeError(f"VK flood limit ({context}): {result!r}")
                logger.warning(
                    "vk flood response context=%s attempt=%s delay=%.2fs",
                    context,
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay_sec)
                continue
            return result
        except Exception as exc:
            if not is_vk_flood_error(exc):
                raise
            last_exc = exc
            if attempt >= max_attempts:
                break
            logger.warning(
                "vk flood exception context=%s attempt=%s delay=%.2fs err=%s",
                context,
                attempt,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay_sec)

    assert last_exc is not None
    raise last_exc
