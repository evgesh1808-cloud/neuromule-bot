"""Telegram API с автоматическим ожиданием при FloodWait (429)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, TypeVar

from aiogram.exceptions import TelegramRetryAfter

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

from platforms.telegram_chat_action import ChatActionName

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def await_with_flood_retry(awaitable: Callable[[], Awaitable[_T]]) -> _T:
    """Повторяет корутину после ``TelegramRetryAfter``."""
    while True:
        try:
            return await awaitable()
        except TelegramRetryAfter as exc:
            wait_sec = max(0.1, float(exc.retry_after))
            logger.info("TelegramRetryAfter: sleep %.1fs", wait_sec)
            await asyncio.sleep(wait_sec)


async def flood_safe_answer(message: "Message", text: str, **kwargs) -> "Message":
    return await await_with_flood_retry(lambda: message.answer(text, **kwargs))


@asynccontextmanager
async def flood_safe_chat_action_loop(
    bot: "Bot",
    chat_id: int,
    action: ChatActionName,
    interval: float = 4.5,
) -> AsyncIterator[None]:
    """``chat_action_loop`` с retry на ``send_chat_action`` при FloodWait."""
    stop_event = asyncio.Event()

    async def _runner() -> None:
        while not stop_event.is_set():
            try:
                await await_with_flood_retry(
                    lambda: bot.send_chat_action(chat_id, action)
                )
            except Exception:
                logger.debug("flood_safe chat_action failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(_runner())
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
