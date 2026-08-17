"""Сборка Telegram media_group (альбомов) в один вызов handler."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)

DEFAULT_ALBUM_LATENCY_SEC = 1.0

_albums: dict[str, list[Message]] = {}
_flush_tasks: dict[str, asyncio.Task[None]] = {}
_pending_album_user_ids: set[int] = set()


def album_collection_pending(user_id: int) -> bool:
    """True, пока для пользователя ещё собирается media_group."""
    return user_id in _pending_album_user_ids


def reset_media_group_state_for_tests() -> None:
    _albums.clear()
    for task in _flush_tasks.values():
        task.cancel()
    _flush_tasks.clear()
    _pending_album_user_ids.clear()


class MediaGroupMiddleware(BaseMiddleware):
    """
    Debounce media_group: собирает все части альбома и один раз вызывает handler
    с ``album_messages`` (отсортированы по ``message_id``).
    """

    def __init__(self, latency: float = DEFAULT_ALBUM_LATENCY_SEC) -> None:
        self.latency = max(0.1, float(latency))

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        album_id = event.media_group_id
        user_id = event.from_user.id if event.from_user else None
        if user_id is not None:
            _pending_album_user_ids.add(user_id)

        _albums.setdefault(album_id, []).append(event)

        prev = _flush_tasks.pop(album_id, None)
        if prev is not None:
            prev.cancel()

        async def _flush(aid: str, trigger: Message, payload: dict[str, Any]) -> None:
            uid = trigger.from_user.id if trigger.from_user else None
            try:
                try:
                    await asyncio.sleep(self.latency)
                except asyncio.CancelledError:
                    return
                messages = _albums.pop(aid, [])
                _flush_tasks.pop(aid, None)
                if not messages:
                    return
                messages.sort(key=lambda item: item.message_id or 0)
                payload["album_messages"] = messages
                logger.info(
                    "media_group collected id=%s count=%s uid=%s",
                    aid,
                    len(messages),
                    uid,
                )
                await handler(trigger, payload)
            except Exception:
                logger.exception(
                    "media_group handler failed id=%s uid=%s",
                    aid,
                    uid,
                )
                if trigger.from_user is not None:
                    try:
                        from content import messages as msg

                        await trigger.answer(msg.TXT_PHOTO_COMPOSITE_FAILED, parse_mode="HTML")
                    except Exception:
                        logger.debug("media_group: failed to notify user", exc_info=True)
            finally:
                if uid is not None:
                    _pending_album_user_ids.discard(uid)

        task = asyncio.create_task(_flush(album_id, event, data))
        _flush_tasks[album_id] = task
        return None
