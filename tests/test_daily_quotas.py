"""Юнит-тесты daily_quotas и FREE image pipeline."""

from __future__ import annotations

from datetime import date

import pytest

from services.billing.daily_quotas import (
    FREE_PHOTO_QUOTA_KEY,
    GLOBAL_FREE_IMAGE_QUOTA_KEY,
    GLOBAL_QUOTA_USER_ID,
    get_free_photo_snapshot,
    refund_free_photo_quota,
    try_consume_free_photo_quota,
)
from services.billing.image_pipeline import (
    FREE_PHOTO_MODEL_KEY,
    build_image_spend_plan,
    free_tier_image_model,
)
from services.billing.types import TariffTier


def test_free_photo_model_default() -> None:
    assert free_tier_image_model() == FREE_PHOTO_MODEL_KEY
    assert FREE_PHOTO_QUOTA_KEY == "free_banana_daily"


def test_free_tier_blocks_free_photo_on_free_tariff() -> None:
    today = date.today().isoformat()
    plan = build_image_spend_plan(
        TariffTier.FREE,
        FREE_PHOTO_MODEL_KEY,
        daily_count=0,
        daily_date=today,
    )
    assert plan.blocked is True
    assert plan.use_free_daily_slot is False


def test_free_tier_blocked_for_premium_model() -> None:
    plan = build_image_spend_plan(
        TariffTier.FREE,
        "nano_banana2",
        daily_count=0,
        daily_date=None,
    )
    assert plan.blocked is True


@pytest.mark.asyncio
async def test_consume_and_refund_free_photo_quota(repo_module) -> None:
    uid = 99001
    await repo_module.ensure_user(uid)

    day = await try_consume_free_photo_quota(uid)
    assert day is not None

    snap = await get_free_photo_snapshot(uid, day=day)
    assert snap.used == 1
    assert snap.remaining == 0

    second = await try_consume_free_photo_quota(uid)
    assert second is None

    await refund_free_photo_quota(uid, quota_date=day)
    snap2 = await get_free_photo_snapshot(uid, day=day)
    assert snap2.used == 0


@pytest.mark.asyncio
async def test_global_cap_blocks_consume(repo_module) -> None:
    import aiosqlite

    from config import settings
    from services.billing.daily_quotas import ensure_quota_schema, quota_day
    from services.repository import DB_PATH

    object.__setattr__(settings, "global_free_image_daily_cap", 1)

    d = quota_day()
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_quota_schema(db)
        await db.execute(
            """
            INSERT INTO user_daily_quotas (user_id, quota_key, quota_date, used_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id, quota_key, quota_date) DO UPDATE SET used_count = 1
            """,
            (GLOBAL_QUOTA_USER_ID, GLOBAL_FREE_IMAGE_QUOTA_KEY, d),
        )
        await db.commit()

    uid = 99002
    await repo_module.ensure_user(uid)
    assert await try_consume_free_photo_quota(uid) is None

    snap = await get_free_photo_snapshot(uid, day=d)
    assert snap.used == 0


@pytest.mark.asyncio
async def test_get_remaining_global_banana_slots(repo_module, monkeypatch) -> None:
    import aiosqlite

    from config import settings
    from services.billing.daily_quotas import (
        ensure_quota_schema,
        get_remaining_global_banana_slots,
        quota_day,
    )
    from services.repository import DB_PATH

    object.__setattr__(settings, "global_free_image_daily_cap", 1500)
    d = quota_day()
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_quota_schema(db)
        await db.execute(
            """
            INSERT INTO user_daily_quotas (user_id, quota_key, quota_date, used_count)
            VALUES (?, ?, ?, 250)
            ON CONFLICT(user_id, quota_key, quota_date) DO UPDATE SET used_count = 250
            """,
            (GLOBAL_QUOTA_USER_ID, GLOBAL_FREE_IMAGE_QUOTA_KEY, d),
        )
        await db.commit()

    assert await get_remaining_global_banana_slots(day=d) == 1250
