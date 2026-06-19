"""God Mode: обход биллинга, лимитов совета дня и подписки для ADMIN_IDS."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from config import settings
from services.billing.chat_pipeline import resolve_effective_text_role
from services.billing.store import atomic_spend, refund_charge
from services.billing.types import SpendFeature, TariffTier, UserBillingState
from services.god_mode import (
    GOD_MODE_CHARGE_ID,
    billing_bypass,
    is_god_mode_charge,
    is_super_admin,
)
from tests.conftest import TEST_ADMIN_IDS

GOD_ADMIN_ID = TEST_ADMIN_IDS[0]
NON_ADMIN_ID = 888_888


@pytest.fixture(autouse=True)
def _enable_god_mode_flag() -> Iterator[None]:
    """God Mode тесты требуют GOD_MODE_ENABLED=1."""
    original = settings.god_mode_enabled
    object.__setattr__(settings, "god_mode_enabled", True)
    try:
        yield
    finally:
        object.__setattr__(settings, "god_mode_enabled", original)


def test_is_super_admin() -> None:
    assert is_super_admin(GOD_ADMIN_ID) is True
    assert is_super_admin(NON_ADMIN_ID) is False


def test_billing_bypass_requires_flag() -> None:
    object.__setattr__(settings, "god_mode_enabled", False)
    try:
        assert billing_bypass(GOD_ADMIN_ID) is False
    finally:
        object.__setattr__(settings, "god_mode_enabled", True)
    assert billing_bypass(GOD_ADMIN_ID) is True


async def test_atomic_spend_skips_balance(repo_module) -> None:
    await repo_module.ensure_user(GOD_ADMIN_ID)
    await repo_module.update_balance(GOD_ADMIN_ID, "crystals", 0)

    charge = await atomic_spend(
        GOD_ADMIN_ID,
        SpendFeature.MUSIC.value,
        energy_need=0,
        crystal_need=15,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    assert charge is not None
    assert charge.charge_id == GOD_MODE_CHARGE_ID

    row = await repo_module.get_user_row(GOD_ADMIN_ID)
    assert row.crystals == 0


async def test_atomic_spend_disabled_god_mode_charges(repo_module) -> None:
    await repo_module.ensure_user(GOD_ADMIN_ID)
    await repo_module.update_balance(GOD_ADMIN_ID, "crystals", 0)
    object.__setattr__(settings, "god_mode_enabled", False)
    try:
        charge = await atomic_spend(
            GOD_ADMIN_ID,
            SpendFeature.MUSIC.value,
            energy_need=0,
            crystal_need=15,
            crystals_only=True,
            reserve_photo_slot=False,
            photo_daily_limit=0,
        )
        assert charge is None
    finally:
        object.__setattr__(settings, "god_mode_enabled", True)


async def test_refund_god_mode_charge_is_noop() -> None:
    assert is_god_mode_charge(GOD_MODE_CHARGE_ID) is True
    assert await refund_charge(GOD_MODE_CHARGE_ID) is True


async def test_try_begin_daily_advice_unlimited(repo_module) -> None:
    today = date.today().isoformat()
    await repo_module.ensure_user(GOD_ADMIN_ID)
    await repo_module.commit_daily_advice(GOD_ADMIN_ID)

    state = await repo_module.get_user_row(GOD_ADMIN_ID)
    assert state.last_free_date == today

    assert await repo_module.try_begin_daily_advice(GOD_ADMIN_ID) is True
    assert await repo_module.try_begin_daily_advice(GOD_ADMIN_ID) is True


async def test_check_and_spend_without_balance(repo_module) -> None:
    await repo_module.ensure_user(GOD_ADMIN_ID)
    await repo_module.update_balance(GOD_ADMIN_ID, "crystals", 0)
    assert await repo_module.check_and_spend(GOD_ADMIN_ID, 100) is True
    row = await repo_module.get_user_row(GOD_ADMIN_ID)
    assert row.crystals == 0


def test_chat_pipeline_zero_balance_bypass_for_admin() -> None:
    user = UserBillingState(
        user_id=GOD_ADMIN_ID,
        current_tariff=TariffTier.MINI,
        energy_free=0,
        energy_paid=0,
        crystals=0,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=False,
        photo_daily_date=None,
        photo_daily_count=0,
    )
    role, notice, blocked = resolve_effective_text_role(user, "psychologist")
    assert blocked is None
    assert role == "psychologist"
    assert notice is None


def test_chat_pipeline_tariff_gate_still_applies_for_admin() -> None:
    user = UserBillingState(
        user_id=GOD_ADMIN_ID,
        current_tariff=TariffTier.MINI,
        energy_free=100,
        energy_paid=0,
        crystals=0,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=False,
        photo_daily_date=None,
        photo_daily_count=0,
    )
    role, notice, blocked = resolve_effective_text_role(user, "podcast_doc")
    assert blocked is not None
    assert blocked.block_reason == "role_requires_smart_tariff"
    assert role == "podcast_doc"
    assert notice is None


async def test_channel_subscription_bypass() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from platforms.telegram_subscription import ChannelSubscription

    bot = MagicMock()
    left_member = MagicMock()
    left_member.status = "left"
    bot.get_chat_member = AsyncMock(return_value=left_member)
    sub = ChannelSubscription(bot)
    assert await sub.is_subscribed(GOD_ADMIN_ID) is True
    assert await sub.is_subscribed_cached(GOD_ADMIN_ID) is True
    assert await sub.is_subscribed(NON_ADMIN_ID) is False
