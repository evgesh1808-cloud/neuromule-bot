"""Профиль: блоки доступных генераций, скидки, тариф."""

from __future__ import annotations

import pytest

from config import Settings
from services.use_cases.profile_view import build_user_profile_html


@pytest.mark.asyncio
async def test_profile_free_default(repo_module) -> None:
    uid = 66001
    await repo_module.ensure_user(uid)
    text = await build_user_profile_html(Settings(), uid)
    assert "Мой профиль NeuroMule" in text
    assert "FREE" in text
    assert "Доступно генераций фото прямо сейчас" in text
    assert "Премиум-медиа" in text
    assert "GPT Image 2" in text
    assert "Реферальная программа" in text
    assert f"start=ref{uid}" in text


@pytest.mark.asyncio
async def test_profile_paid_shows_crystals_breakdown(repo_module) -> None:
    uid = 66002
    await repo_module.ensure_user(uid)
    from services.billing import store

    await store.apply_tariff_period_renewal(
        uid, tariff="SMART", energy_paid_grant=1500, sub_crystals_grant=35
    )
    text = await build_user_profile_html(Settings(), uid)
    assert "SMART" in text
    assert "35 по тарифу" in text
    assert "Подписка активна до" in text


@pytest.mark.asyncio
async def test_profile_capacity_zero_diamonds(repo_module) -> None:
    uid = 66003
    await repo_module.ensure_user(uid)
    text = await build_user_profile_html(Settings(), uid)
    assert "нужно ещё <b>70 💎</b>" in text
    assert "нужно ещё <b>50 💎</b>" in text


@pytest.mark.asyncio
async def test_profile_has_no_discount_banner(repo_module) -> None:
    """Скидки полностью выпилены — никакого баннера в кабинете быть не должно."""
    uid = 66004
    await repo_module.ensure_user(uid)
    text = await build_user_profile_html(Settings(), uid)
    assert "скидка" not in text.lower()


@pytest.mark.asyncio
async def test_upscale_fallback_to_energy(repo_module) -> None:
    """UPSCALE при 0 💎 списывает 10 ⚡ резервом."""
    from services.billing import store
    from services.billing.hd_pipeline import spend_upscale

    uid = 66005
    await repo_module.ensure_user(uid)
    await repo_module.update_balance(uid, "energy", 50)
    user_before = await store.load_user_billing(uid)
    assert user_before.crystals == 0
    res = await spend_upscale(uid)
    assert res.ok is True
    user_after = await store.load_user_billing(uid)
    assert user_after.total_energy == user_before.total_energy - 10


@pytest.mark.asyncio
async def test_profile_includes_blogger_constructor_block(repo_module) -> None:
    uid = 66006
    await repo_module.ensure_user(uid)
    text = await build_user_profile_html(Settings(), uid)
    assert "Конструктор «Блогер»" in text
    assert "Адаптация поста:" in text
    assert "AI-обложка:" in text
    assert "3 💎" in text
    assert "4 💎" in text
