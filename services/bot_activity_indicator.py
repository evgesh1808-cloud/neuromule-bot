"""Индикатор «печатает…» для Telegram и VK (фоновая задача + гарантированная отмена)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Literal

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

PlatformKind = Literal["telegram", "vk"]
DEFAULT_TYPING_REFRESH_SEC = 4.5


async def _send_typing(api: object, target_id: int, platform: PlatformKind) -> None:
    if platform == "telegram":
        await api.send_chat_action(target_id, "typing")  # type: ignore[union-attr]
        return
    await api.messages.set_activity(peer_id=target_id, type="typing")  # type: ignore[union-attr]


@asynccontextmanager
async def bot_typing_indicator(
    api: "Bot | object",
    target_id: int,
    *,
    platform: PlatformKind = "telegram",
    interval_sec: float = DEFAULT_TYPING_REFRESH_SEC,
) -> AsyncIterator[None]:
    """
    Периодически шлёт typing, пока выполняется основная корутина.

    * Telegram: ``send_chat_action(chat_id, "typing")`` (``api`` — aiogram ``Bot``).
    * VK: ``messages.set_activity(peer_id=…, type="typing")`` (``api`` — ``bot.api``).

    Фоновая задача создаётся через ``asyncio.create_task`` и отменяется в ``finally``.
    """
    stop_event = asyncio.Event()
    refresh = max(1.0, float(interval_sec))

    async def _runner() -> None:
        try:
            while not stop_event.is_set():
                try:
                    await _send_typing(api, target_id, platform)
                except Exception:
                    logger.debug(
                        "bot_typing_indicator send failed platform=%s target_id=%s",
                        platform,
                        target_id,
                        exc_info=True,
                    )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=refresh)
                    return
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_runner(), name=f"bot_typing_{platform}_{target_id}")
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug(
                "bot_typing_indicator task cleanup failed platform=%s target_id=%s",
                platform,
                target_id,
                exc_info=True,
            )


__all__ = ["bot_typing_indicator", "DEFAULT_TYPING_REFRESH_SEC"]
