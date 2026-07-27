"""FREE chat lock: запрет параллельных запросов."""

from __future__ import annotations

import pytest

from services import repository as repo


@pytest.mark.asyncio
async def test_chat_lock_acquire_blocks_parallel(repo_module) -> None:
    uid = 991001
    assert await repo.chat_lock_acquire(uid, ttl_sec=12) is True
    assert await repo.chat_lock_acquire(uid, ttl_sec=12) is False
    await repo.chat_lock_release(uid)
    assert await repo.chat_lock_acquire(uid, ttl_sec=12) is True
    await repo.chat_lock_release(uid)


@pytest.mark.asyncio
async def test_free_chat_lock_context_manager(repo_module) -> None:
    from config import Settings
    from services.rate_limit_service import free_chat_lock

    s = Settings()
    uid = 991002
    async with free_chat_lock(s, uid, enabled=True, ttl_sec=12) as ok:
        assert ok is True
        async with free_chat_lock(s, uid, enabled=True, ttl_sec=12) as ok2:
            assert ok2 is False
    async with free_chat_lock(s, uid, enabled=True, ttl_sec=12) as ok3:
        assert ok3 is True
    async with free_chat_lock(s, uid, enabled=False, ttl_sec=12) as ok4:
        assert ok4 is True
