"""Highload readiness: key rotator, FSM Redis wiring, atomic crystal CAS."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from aiogram.fsm.storage.memory import MemoryStorage

from config import Settings
from services.billing.chat_pipeline import (
    OpenRouterKeyRotator,
    get_openrouter_key_rotator,
    reset_openrouter_key_rotator,
    resolve_openrouter_api_key,
)


def test_openrouter_key_rotator_round_robin() -> None:
    rot = OpenRouterKeyRotator(["k1", "k2", "k3"])
    assert rot.size == 3
    assert [rot.next_key() for _ in range(6)] == ["k1", "k2", "k3", "k1", "k2", "k3"]


def test_resolve_openrouter_api_key_uses_pool() -> None:
    reset_openrouter_key_rotator()
    s = Settings(
        tg_token="t",
        openrouter_key="primary",
        openrouter_keys=["a", "b"],
    )
    try:
        keys = {resolve_openrouter_api_key(s) for _ in range(6)}
        assert "primary" in keys or "a" in keys
        assert len(keys) >= 2
    finally:
        reset_openrouter_key_rotator()


def test_get_chat_headers_rotates_bearer() -> None:
    from services.ai_text import get_chat_headers

    reset_openrouter_key_rotator()
    s = Settings(tg_token="t", openrouter_key="", openrouter_keys=["alpha", "beta"])
    try:
        h1 = get_chat_headers(s)["Authorization"]
        h2 = get_chat_headers(s)["Authorization"]
        assert h1.startswith("Bearer ")
        assert h2.startswith("Bearer ")
        assert {h1, h2} == {"Bearer alpha", "Bearer beta"}
    finally:
        reset_openrouter_key_rotator()


def test_build_fsm_storage_memory_without_redis() -> None:
    from platforms.telegram_bot import build_fsm_storage

    with patch("platforms.telegram_bot.settings") as mock_settings:
        mock_settings.redis_url = ""
        storage = build_fsm_storage()
    assert isinstance(storage, MemoryStorage)


def test_build_fsm_storage_redis_when_url_set() -> None:
    from aiogram.fsm.storage.redis import RedisStorage
    from platforms.telegram_bot import build_fsm_storage

    with patch("platforms.telegram_bot.settings") as mock_settings:
        mock_settings.redis_url = "redis://localhost:6379/15"
        storage = build_fsm_storage()
    assert isinstance(storage, RedisStorage)


@pytest.mark.asyncio
async def test_atomic_spend_crystal_cannot_go_negative(repo_module) -> None:
    from services.billing.store import atomic_spend
    from services.billing.types import SpendFeature

    uid = 99001
    await repo_module.ensure_user(uid)
    # Обнуляем энергию, кладём ровно 2 💎.
    import aiosqlite

    from services.repository import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                energy_free = 0, energy_paid = 0, energy = 0,
                sub_crystals = 0, buy_crystals = 2,
                crystals = 2, balance = 2, balance_crystals = 2
            WHERE id = ?
            """,
            (uid,),
        )
        await db.commit()

    ok = await atomic_spend(
        uid,
        SpendFeature.CHAT.value,
        energy_need=0,
        crystal_need=2,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    assert ok is not None
    assert ok.crystals == 2

    fail = await atomic_spend(
        uid,
        SpendFeature.CHAT.value,
        energy_need=0,
        crystal_need=1,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    assert fail is None
    row = await repo_module.get_user_row(uid)
    assert int(getattr(row, "crystals", 0) or 0) == 0
