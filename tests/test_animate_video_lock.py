"""Video generation lock for animate jobs."""

import pytest


@pytest.mark.asyncio
async def test_animate_video_lock_table_auto_created_on_old_db(repo_module) -> None:
    import aiosqlite

    from services.repository import animate_video_lock_acquire, ensure_animate_video_locks_table

    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute("DROP TABLE IF EXISTS animate_video_locks")
        await db.commit()

    await ensure_animate_video_locks_table()
    assert await animate_video_lock_acquire(88002, ttl_sec=30) is True


@pytest.mark.asyncio
async def test_animate_video_lock_blocks_parallel(repo_module) -> None:
    from services.repository import animate_video_lock_acquire, animate_video_lock_is_active, animate_video_lock_release

    uid = 88001
    assert await animate_video_lock_acquire(uid, ttl_sec=30) is True
    assert await animate_video_lock_is_active(uid) is True
    assert await animate_video_lock_acquire(uid, ttl_sec=30) is False
    await animate_video_lock_release(uid)
    assert await animate_video_lock_is_active(uid) is False
    assert await animate_video_lock_acquire(uid, ttl_sec=30) is True
    await animate_video_lock_release(uid)
