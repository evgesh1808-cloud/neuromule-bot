"""Сборка Telegram media_group (альбомов) в один вызов handler."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)

DEFAULT_ALBUM_LATENCY_SEC = 0.5


class MediaGroupMiddleware(BaseMiddleware):
    """
    Debounce media_group: собирает все части альбома и один раз вызывает handler
    с ``album_messages`` (отсортированы по ``message_id``).
    """

    def __init__(self, latency: float = DEFAULT_ALBUM_LATENCY_SEC) -> None:
        self.latency = max(0.1, float(latency))
        self._albums: dict[str, list[Message]] = {}
        self._flush_tasks: dict[str, asyncio.Task[None]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        album_id = event.media_group_id
        self._albums.setdefault(album_id, []).append(event)

        prev = self._flush_tasks.pop(album_id, None)
        if prev is not None:
            prev.cancel()

        async def _flush(aid: str, trigger: Message, payload: dict[str, Any]) -> None:
            try:
                await asyncio.sleep(self.latency)
            except asyncio.CancelledError:
                return
            messages = self._albums.pop(aid, [])
            self._flush_tasks.pop(aid, None)
            if not messages:
                return
            messages.sort(key=lambda item: item.message_id or 0)
            payload["album_messages"] = messages
            logger.debug(
                "media_group collected id=%s count=%s",
                aid,
                len(messages),
            )
            await handler(trigger, payload)

        task = asyncio.create_task(_flush(album_id, event, data))
        self._flush_tasks[album_id] = task
        return None
