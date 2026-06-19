"""Строгая проверка баланса перед OpenRouter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from content.messages import TXT_CHAT_ROLE_FALLBACK_STANDARD
from services.billing.chat_pipeline import (
    can_afford_role_minimum,
    is_zero_chat_balance,
    resolve_effective_text_role,
    resolve_and_charge_text_chat,
)
from services.billing.types import TariffTier, UserBillingState


def _user(
    *,
    energy_free: int = 0,
    energy_paid: int = 0,
    crystals: int = 0,
    tariff: TariffTier = TariffTier.FREE,
) -> UserBillingState:
    return UserBillingState(
        user_id=1,
        current_tariff=tariff,
        energy_free=energy_free,
        energy_paid=energy_paid,
        crystals=crystals,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=False,
        photo_daily_date=None,
        photo_daily_count=0,
    )


def test_can_afford_standard_energy_or_crystals() -> None:
    u = _user(energy_free=1)
    assert can_afford_role_minimum(u, "standard") is True
    u2 = _user(crystals=1)
    assert can_afford_role_minimum(u2, "standard") is True
    assert can_afford_role_minimum(_user(), "standard") is False


def test_can_afford_expert_minimum() -> None:
    assert can_afford_role_minimum(_user(energy_free=5), "psychologist") is True
    assert can_afford_role_minimum(_user(crystals=3), "academic") is True
    assert can_afford_role_minimum(_user(energy_free=4), "psychologist") is False
    assert can_afford_role_minimum(_user(crystals=2), "academic") is False


def test_resolve_expert_fallback_to_standard() -> None:
    user = _user(energy_free=1)
    role, notice, blocked = resolve_effective_text_role(user, "psychologist")
    assert role == "standard"
    assert notice == TXT_CHAT_ROLE_FALLBACK_STANDARD
    assert blocked is None


def test_resolve_zero_balance_blocks() -> None:
    user = _user()
    role, notice, blocked = resolve_effective_text_role(user, "standard")
    assert blocked is not None
    assert blocked.blocked is True
    assert blocked.block_reason == "zero_balance"
    assert notice is None


@pytest.mark.asyncio
async def test_resolve_and_charge_spends_before_api() -> None:
    user = _user(energy_free=10)
    with patch(
        "services.billing.chat_pipeline.store.load_user_billing",
        new_callable=AsyncMock,
        return_value=user,
    ), patch(
        "services.billing.chat_pipeline.store.atomic_spend",
        new_callable=AsyncMock,
    ) as spend:
        from services.billing.types import ChargeBreakdown

        spend.return_value = ChargeBreakdown(charge_id="c1", energy_free=1)
        result = await resolve_and_charge_text_chat(1, "standard")
        assert result.charge_id == "c1"
        assert result.plan.blocked is False
        spend.assert_awaited_once()
        assert spend.await_args.kwargs["energy_need"] == 1
