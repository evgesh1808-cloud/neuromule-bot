"""Юнит-тесты Billing Manager (без внешних API)."""

from __future__ import annotations

import pytest

from services.billing.image_pipeline import build_image_spend_plan, normalize_image_model
from services.billing.pricing import SHOP_PACKS
from services.billing.shop import normalize_pack_name
from services.billing.types import TariffTier
from services.billing.video_pipeline import VIDEO_SCENARIOS, resolve_video_route


def test_normalize_pack_names() -> None:
    assert normalize_pack_name("MINI") == "MINI"
    assert normalize_pack_name("crystals_40") == "crystals_40"
    assert normalize_pack_name("unknown") is None


def test_shop_packs_have_prices() -> None:
    assert SHOP_PACKS["ULTRA"]["crystals"] == 120
    assert SHOP_PACKS["crystals_10"]["crystals"] == 10


def test_free_imagen_plan_uses_slot() -> None:
    plan = build_image_spend_plan(TariffTier.FREE, "imagen4", daily_count=1, daily_date="2026-05-19")
    assert plan.use_free_daily_slot is True
    assert plan.crystal_cost == 0


def test_paid_flux_energy_or_crystals() -> None:
    plan = build_image_spend_plan(TariffTier.SMART, "flux_schnell", daily_count=0, daily_date=None)
    assert plan.energy_cost == 30
    assert plan.crystal_cost == 3


def test_dalle_crystals_only() -> None:
    plan = build_image_spend_plan(TariffTier.ULTRA, "gpt_image2", daily_count=0, daily_date=None)
    assert plan.crystals_only is True
    assert plan.crystal_cost == 5


def test_video_scenario_registry_size() -> None:
    assert len(VIDEO_SCENARIOS) >= 30
    assert VIDEO_SCENARIOS["pain_homework_explosion"].crystal_cost == 50
    assert VIDEO_SCENARIOS["face_vip_shrek"].crystal_cost == 100


def test_video_route_ultra_priority() -> None:
    route = resolve_video_route("video_pro_5sec", TariffTier.ULTRA)
    assert route is not None
    assert route.queue_priority == 1
    assert route.crystal_cost == 35


def test_image_model_aliases() -> None:
    assert normalize_image_model("flux-schnell") == "flux_schnell"
    assert normalize_image_model("imagen4") == "imagen4"


@pytest.mark.asyncio
async def test_atomic_spend_and_refund_chat(repo_module) -> None:
    from services.billing.store import atomic_spend, refund_charge
    from services.billing.types import SpendFeature

    uid = 88100
    await repo_module.ensure_user(uid)
    charge = await atomic_spend(
        uid,
        SpendFeature.CHAT.value,
        energy_need=1,
        crystal_need=0,
        crystals_only=False,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    assert charge is not None
    row = await repo_module.get_user_row(uid)
    assert row.energy == 29
    assert await refund_charge(charge.charge_id)
    row2 = await repo_module.get_user_row(uid)
    assert row2.energy == 30


@pytest.mark.asyncio
async def test_process_purchase_mini(repo_module) -> None:
    from services.billing.shop import process_purchase

    uid = 88099
    await repo_module.ensure_user(uid)
    result = await process_purchase(uid, "MINI")
    assert result.ok
    assert result.energy_paid_added == 500
    assert result.crystals_added == 10
    row = await repo_module.get_user_row(uid)
    assert row.tariff.upper() == "MINI"
