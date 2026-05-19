"""Проверка подписки на канал с кэшем."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from aiogram import Bot

from config import settings


class ChannelSubscription:
    def __init__(self, bot: Bot, *, ttl_sec: float = 60.0) -> None:
        self._bot = bot
        self._ttl = ttl_sec
        self._cache: dict[int, float] = {}

    async def is_subscribed(self, user_id: int) -> bool:
        try:
            member = await self._bot.get_chat_member(chat_id=settings.channel_id, user_id=user_id)
            return member.status not in ("left", "kicked")
        except Exception:
            return True

    async def is_subscribed_cached(self, user_id: int) -> bool:
        now = time.monotonic()
        cached_at = self._cache.get(user_id)
        if cached_at is not None and (now - cached_at) < self._ttl:
            return True
        ok = await self.is_subscribed(user_id)
        if ok:
            self._cache[user_id] = now
        else:
            self._cache.pop(user_id, None)
        return ok

    def invalidate(self, user_id: int) -> None:
        self._cache.pop(user_id, None)

    def as_is_subscribed(self) -> Callable[[int], Awaitable[bool]]:
        return self.is_subscribed
