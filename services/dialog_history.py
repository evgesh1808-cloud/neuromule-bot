"""Последовательная запись реплик ассистента и prune в SQLite (снижает конкуренцию по БД)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_lock = asyncio.Lock()


@asynccontextmanager
async def serialized_assistant_commit() -> AsyncIterator[None]:
    """Один коммит «assistant + prune» за раз по всем пользователям."""
    async with _lock:
        yield
