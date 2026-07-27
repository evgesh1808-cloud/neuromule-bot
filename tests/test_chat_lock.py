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


@pytest.mark.asyncio
async def test_claim_chat_busy_notice_cooldown(repo_module) -> None:
    from unittest.mock import MagicMock

    from services import rate_limit_service as rls

    s = MagicMock()
    s.redis_url = ""
    uid = 991003
    rls._BUSY_NOTICE_UNTIL.pop(uid, None)

    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is True
    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is False
    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is False

    # Истёкший кулдаун снова разрешает уведомление.
    rls._BUSY_NOTICE_UNTIL[uid] = 0.0
    assert await rls.claim_chat_busy_notice(s, uid, cooldown_sec=2) is True


@pytest.mark.asyncio
async def test_remember_and_pop_chat_busy_message_id(repo_module) -> None:
    from unittest.mock import MagicMock

    from services import rate_limit_service as rls

    s = MagicMock()
    s.redis_url = ""
    uid = 991004
    rls._BUSY_NOTICE_MSG_ID.pop(uid, None)

    await rls.remember_chat_busy_message_id(s, uid, 4242)
    assert rls._BUSY_NOTICE_MSG_ID.get(uid) == 4242
    assert await rls.pop_chat_busy_message_id(s, uid) == 4242
    assert await rls.pop_chat_busy_message_id(s, uid) is None
