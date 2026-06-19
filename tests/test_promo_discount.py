"""Подарочные промокоды: гейт по тарифу и начисление ⚡/💎."""

from __future__ import annotations

import pytest

from services.promo_discount import is_tariff_allowed
from services.repository import (
    add_promo_code,
    get_user_row,
    try_redeem_promo,
)
from services.use_cases.payment_shop_turn import build_tariffs_main_text
from services.use_cases.promo_turn import PromoOutcome, run_promo_redeem


def test_is_tariff_allowed_paid_only() -> None:
    assert is_tariff_allowed("FREE", "SMART,ULTRA") is False
    assert is_tariff_allowed("SMART", "SMART,ULTRA") is True
    assert is_tariff_allowed("Free", "FREE,MINI,SMART,ULTRA") is True


def test_tariff_prices_are_strict_fixed() -> None:
    """Цены тарифов всегда фиксированные — независимо от каких-либо параметров."""
    text = build_tariffs_main_text()
    assert "349" in text
    assert "790" in text
    assert "2490" in text
    assert "скидка" not in text.lower()


@pytest.mark.asyncio
async def test_promo_tariff_blocked_for_free_user(repo_module) -> None:
    uid = 88001
    await repo_module.ensure_user(uid)
    await add_promo_code(
        "PAIDONLY",
        reward=0,
        uses=10,
        allowed_tariffs="SMART,ULTRA",
        crystal_bonus=10,
    )
    ok, key, *_ = await try_redeem_promo(uid, "PAIDONLY")
    assert not ok
    assert key == "tariff_blocked"


@pytest.mark.asyncio
async def test_gift_code_grants_crystals_eternal(repo_module) -> None:
    """Подарочный код +10 💎 идёт в вечный баланс (buy_crystals)."""
    uid = 88002
    await repo_module.ensure_user(uid)
    await add_promo_code("GIFT10", reward=0, uses=5, crystal_bonus=10)
    pr = await run_promo_redeem(uid, "GIFT10")
    assert pr.outcome is PromoOutcome.REDEEMED
    assert pr.bonus_crystals == 10
    row = await get_user_row(uid)
    assert row.buy_crystals == 10


@pytest.mark.asyncio
async def test_gift_code_grants_energy_paid(repo_module) -> None:
    """Подарочный код на энергию идёт в paid-баланс (не сгорает в полночь)."""
    uid = 88003
    await repo_module.ensure_user(uid)
    await add_promo_code("BONUS50", reward=50, uses=3)
    pr = await run_promo_redeem(uid, "BONUS50")
    assert pr.outcome is PromoOutcome.REDEEMED
    assert pr.bonus_energy == 50
    row = await get_user_row(uid)
    assert row.energy >= 50


@pytest.mark.asyncio
async def test_gift_code_double_redeem_blocked(repo_module) -> None:
    uid = 88004
    await repo_module.ensure_user(uid)
    await add_promo_code("ONCE", reward=10, uses=5)
    first = await run_promo_redeem(uid, "ONCE")
    assert first.outcome is PromoOutcome.REDEEMED
    second = await run_promo_redeem(uid, "ONCE")
    assert second.outcome is PromoOutcome.USED


@pytest.mark.asyncio
async def test_gift_code_combo_energy_and_crystals(repo_module) -> None:
    uid = 88005
    await repo_module.ensure_user(uid)
    await add_promo_code("COMBO", reward=25, uses=2, crystal_bonus=5)
    pr = await run_promo_redeem(uid, "COMBO")
    assert pr.outcome is PromoOutcome.REDEEMED
    assert pr.bonus_energy == 25
    assert pr.bonus_crystals == 5
