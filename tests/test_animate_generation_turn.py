"""Integration: run_animate_generation_turn + refund on lock race."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from services.use_cases.animate_generation_turn import (
    AnimateGenOutcome,
    run_animate_generation_turn,
)


@pytest.mark.asyncio
async def test_animate_turn_success_enqueues_and_notifies(repo_module) -> None:
    from config import settings
    from services.billing import init_billing_schema

    uid = 77001
    await repo_module.ensure_user(uid, username="anim_test")
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tariff = ?, buy_crystals = ?, crystals = ? WHERE id = ?",
            ("Ultra", 100, 100, uid),
        )
        await db.commit()
    await init_billing_schema()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch("services.use_cases.animate_generation_turn.fire_animate_job") as fire:
        result = await run_animate_generation_turn(
            uid=uid,
            telegram_file_id="AgAC_photo_ok",
            bot=bot,
            chat_id=uid,
            settings=settings,
        )

    assert result.outcome is AnimateGenOutcome.SUCCESS
    fire.assert_called_once()
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_animate_turn_refunds_when_lock_busy_after_spend(repo_module) -> None:
    from config import settings
    from services.billing import init_billing_schema
    from services.repository import animate_video_lock_acquire

    uid = 77002
    await repo_module.ensure_user(uid, username="anim_lock")
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tariff = ?, buy_crystals = ?, crystals = ? WHERE id = ?",
            ("Ultra", 100, 100, uid),
        )
        await db.commit()
    await init_billing_schema()
    assert await animate_video_lock_acquire(uid, ttl_sec=300) is True

    bot = MagicMock()
    bot.send_message = AsyncMock()

    result = await run_animate_generation_turn(
        uid=uid,
        telegram_file_id="AgAC_photo_ok",
        bot=bot,
        chat_id=uid,
        settings=settings,
    )

    assert result.outcome is AnimateGenOutcome.ALREADY_GENERATING
    bot.send_message.assert_not_awaited()

@pytest.mark.asyncio
async def test_animate_turn_refunds_crystals_when_lock_acquire_fails(repo_module) -> None:
    from config import settings
    from services.billing import init_billing_schema

    uid = 77003
    await repo_module.ensure_user(uid, username="anim_refund")
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tariff = ?, buy_crystals = ?, crystals = ? WHERE id = ?",
            ("Ultra", 100, 100, uid),
        )
        await db.commit()
    await init_billing_schema()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch(
        "services.use_cases.animate_generation_turn.try_acquire_animate_video_lock",
        AsyncMock(return_value=False),
    ):
        result = await run_animate_generation_turn(
            uid=uid,
            telegram_file_id="AgAC_photo_ok",
            bot=bot,
            chat_id=uid,
            settings=settings,
        )

    assert result.outcome is AnimateGenOutcome.ALREADY_GENERATING
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        async with db.execute(
            "SELECT buy_crystals FROM users WHERE id = ?", (uid,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 100


@pytest.mark.asyncio
async def test_animate_turn_internal_error_refunds_after_enqueue_failure(repo_module) -> None:
    from config import settings
    from services.billing import init_billing_schema

    uid = 77004
    await repo_module.ensure_user(uid, username="anim_internal")
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tariff = ?, buy_crystals = ?, crystals = ? WHERE id = ?",
            ("Ultra", 100, 100, uid),
        )
        await db.commit()
    await init_billing_schema()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch(
        "services.use_cases.animate_generation_turn.fire_animate_job",
        side_effect=RuntimeError("queue down"),
    ):
        result = await run_animate_generation_turn(
            uid=uid,
            telegram_file_id="AgAC_photo_ok",
            bot=bot,
            chat_id=uid,
            settings=settings,
        )

    assert result.outcome is AnimateGenOutcome.INTERNAL_ERROR
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        async with db.execute(
            "SELECT buy_crystals FROM users WHERE id = ?", (uid,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 100
