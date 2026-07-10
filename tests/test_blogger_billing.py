"""Тесты биллинга конструктора «Блогер»."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.billing.blogger_pipeline import (
    can_afford_blogger_adapt,
    can_afford_blogger_cover,
    spend_blogger_adapt,
    spend_blogger_cover,
)
from services.billing.pricing_constants import BLOGGER_ADAPT_COST, BLOGGER_COVER_COST
from services.billing.types import ChargeBreakdown, UserBillingState, TariffTier


def test_blogger_pricing_constants() -> None:
    assert BLOGGER_ADAPT_COST == 3
    assert BLOGGER_COVER_COST == 4


def _billing_user(crystals: int) -> UserBillingState:
    return UserBillingState(
        user_id=1,
        current_tariff=TariffTier.FREE,
        energy_free=0,
        energy_paid=0,
        crystals=crystals,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=False,
        photo_daily_date=None,
        photo_daily_count=0,
    )


@pytest.mark.asyncio
async def test_can_afford_blogger_cover_with_zero_balance() -> None:
    with patch(
        "services.billing.blogger_pipeline.store.load_user_billing",
        AsyncMock(return_value=_billing_user(0)),
    ):
        assert await can_afford_blogger_cover(1) is False
        assert await can_afford_blogger_adapt(1) is False


@pytest.mark.asyncio
async def test_can_afford_blogger_cover_with_four_crystals() -> None:
    with patch(
        "services.billing.blogger_pipeline.store.load_user_billing",
        AsyncMock(return_value=_billing_user(4)),
    ):
        assert await can_afford_blogger_cover(1) is True


@pytest.mark.asyncio
async def test_spend_blogger_cover_calls_atomic_spend() -> None:
    charge = ChargeBreakdown(charge_id="c1", crystals=4)
    with patch(
        "services.billing.blogger_pipeline.store.atomic_spend",
        AsyncMock(return_value=charge),
    ) as mock_spend:
        result = await spend_blogger_cover(42)
    assert result.ok is True
    assert result.charge == charge
    mock_spend.assert_awaited_once()
    kwargs = mock_spend.await_args.kwargs
    assert kwargs["crystal_need"] == 4
    assert kwargs["crystals_only"] is True


@pytest.mark.asyncio
async def test_spend_blogger_adapt_calls_atomic_spend() -> None:
    charge = ChargeBreakdown(charge_id="a1", crystals=3)
    with patch(
        "services.billing.blogger_pipeline.store.atomic_spend",
        AsyncMock(return_value=charge),
    ) as mock_spend:
        result = await spend_blogger_adapt(42)
    assert result.ok is True
    assert result.charge == charge
    mock_spend.assert_awaited_once()
    kwargs = mock_spend.await_args.kwargs
    assert kwargs["crystal_need"] == 3
    assert kwargs["crystals_only"] is True
