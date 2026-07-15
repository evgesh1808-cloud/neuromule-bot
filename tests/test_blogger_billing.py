"""Тесты биллинга конструктора «Блогер»."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from services.billing.blogger_pipeline import (
    BLOGGER_COVER_IMAGE_MODEL,
    can_afford_blogger_adapt,
    can_afford_blogger_cover,
    spend_blogger_adapt,
    spend_blogger_cover,
)
from services.billing.pricing_constants import BLOGGER_ADAPT_COST
from services.billing.types import ChargeBreakdown, UserBillingState, TariffTier


def test_blogger_pricing_constants() -> None:
    assert BLOGGER_ADAPT_COST == 3
    assert BLOGGER_COVER_IMAGE_MODEL == "flux_schnell"


def _billing_user(
    *,
    crystals: int = 0,
    energy_free: int = 0,
    tariff: TariffTier = TariffTier.FREE,
    photo_daily_count: int = 0,
    photo_daily_date: str | None = None,
) -> UserBillingState:
    return UserBillingState(
        user_id=1,
        current_tariff=tariff,
        energy_free=energy_free,
        energy_paid=0,
        crystals=crystals,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=False,
        photo_daily_date=photo_daily_date,
        photo_daily_count=photo_daily_count,
    )


@pytest.mark.asyncio
async def test_can_afford_blogger_cover_free_slot_available() -> None:
    with patch(
        "services.billing.blogger_pipeline.store.load_user_billing",
        AsyncMock(return_value=_billing_user(crystals=0, photo_daily_count=0)),
    ):
        assert await can_afford_blogger_cover(1) is True


@pytest.mark.asyncio
async def test_can_afford_blogger_cover_free_slot_exhausted_without_crystals() -> None:
    today = date.today().isoformat()
    with patch(
        "services.billing.blogger_pipeline.store.load_user_billing",
        AsyncMock(return_value=_billing_user(crystals=0, photo_daily_count=3, photo_daily_date=today)),
    ):
        assert await can_afford_blogger_cover(1) is False


@pytest.mark.asyncio
async def test_can_afford_blogger_cover_free_overlimit_with_crystals() -> None:
    today = date.today().isoformat()
    with patch(
        "services.billing.blogger_pipeline.store.load_user_billing",
        AsyncMock(return_value=_billing_user(crystals=3, photo_daily_count=3, photo_daily_date=today)),
    ):
        assert await can_afford_blogger_cover(1) is True


@pytest.mark.asyncio
async def test_can_afford_blogger_adapt_with_zero_balance() -> None:
    with patch(
        "services.billing.blogger_pipeline.store.load_user_billing",
        AsyncMock(return_value=_billing_user(crystals=0)),
    ):
        assert await can_afford_blogger_cover(1) is True  # free slot
        assert await can_afford_blogger_adapt(1) is False


@pytest.mark.asyncio
async def test_spend_blogger_cover_delegates_to_image_pipeline() -> None:
    charge = ChargeBreakdown(charge_id="c1", crystals=0)
    with patch(
        "services.billing.blogger_pipeline.spend_image_resource",
        AsyncMock(return_value=type("Spend", (), {"ok": True, "charge": charge, "error": ""})()),
    ) as mock_spend:
        result = await spend_blogger_cover(42)
    assert result.ok is True
    mock_spend.assert_awaited_once_with(42, "flux_schnell")


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
