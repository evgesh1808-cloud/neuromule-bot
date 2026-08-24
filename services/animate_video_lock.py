"""Lock «видео монтируется» — защита от параллельных animate-job на user_id."""

from __future__ import annotations

import logging
import time

from config import Settings

logger = logging.getLogger(__name__)

DEFAULT_ANIMATE_LOCK_TTL_SEC = 900


async def is_animate_video_locked(user_id: int) -> bool:
    from services.repository import animate_video_lock_is_active

    return await animate_video_lock_is_active(user_id)


async def try_acquire_animate_video_lock(
    user_id: int,
    *,
    ttl_sec: int = DEFAULT_ANIMATE_LOCK_TTL_SEC,
    settings: Settings | None = None,
) -> bool:
    _ = settings
    from services.repository import animate_video_lock_acquire

    ok = await animate_video_lock_acquire(user_id, ttl_sec=max(60, int(ttl_sec)))
    if ok:
        logger.info("animate video lock acquired uid=%s ttl=%ss", user_id, ttl_sec)
    return ok


async def release_animate_video_lock(user_id: int) -> None:
    from services.repository import animate_video_lock_release

    await animate_video_lock_release(user_id)
    logger.debug("animate video lock released uid=%s", user_id)


async def refresh_animate_video_lock(user_id: int, *, ttl_sec: int = DEFAULT_ANIMATE_LOCK_TTL_SEC) -> None:
    """Продлевает TTL (best-effort) пока job в очереди."""
    import aiosqlite

    from services.repository import DB_PATH

    expires = time.time() + max(60, int(ttl_sec))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE animate_video_locks SET expires_at = ? WHERE user_id = ?",
            (expires, user_id),
        )
        await db.commit()
