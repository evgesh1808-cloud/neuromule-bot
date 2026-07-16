"""Флаг show_suggested_replies: дефолты по тарифу + клавиатура профиля."""

from __future__ import annotations

import pytest

from content import messages as msg
from platforms.telegram_keyboards import cabinet_keyboard
from services.billing.types import TariffTier


@pytest.mark.asyncio
async def test_default_suggested_replies_free_on_paid_off(repo_module) -> None:
    free_uid, paid_uid = 77001, 77002
    await repo_module.ensure_user(free_uid)
    await repo_module.ensure_user(paid_uid)
    from services.billing import store

    await store.apply_tariff_period_renewal(
        paid_uid, tariff="MINI", energy_paid_grant=500, sub_crystals_grant=10
    )

    assert await repo_module.get_show_suggested_replies(free_uid) is True
    assert await repo_module.get_show_suggested_replies(paid_uid) is False
    assert repo_module.default_show_suggested_replies("FREE") is True
    assert repo_module.default_show_suggested_replies(TariffTier.SMART.value) is False


@pytest.mark.asyncio
async def test_toggle_suggested_replies_persists_for_paid(repo_module) -> None:
    uid = 77003
    await repo_module.ensure_user(uid)
    from services.billing import store

    await store.apply_tariff_period_renewal(
        uid, tariff="SMART", energy_paid_grant=1500, sub_crystals_grant=35
    )
    assert await repo_module.get_show_suggested_replies(uid) is False
    assert await repo_module.set_show_suggested_replies(uid, True) is True
    assert await repo_module.get_show_suggested_replies(uid) is True
    assert await repo_module.set_show_suggested_replies(uid, False) is False
    assert await repo_module.get_show_suggested_replies(uid) is False


@pytest.mark.asyncio
async def test_free_cannot_disable_suggested_replies(repo_module) -> None:
    uid = 77004
    await repo_module.ensure_user(uid)
    assert await repo_module.set_show_suggested_replies(uid, False) is True
    assert await repo_module.get_show_suggested_replies(uid) is True


def test_cabinet_keyboard_hides_toggle_on_free() -> None:
    kb = cabinet_keyboard(show_suggested_replies=None)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert msg.CB_TOGGLE_SUGGESTED_REPLIES not in flat


def test_cabinet_keyboard_shows_dynamic_toggle_label() -> None:
    off_kb = cabinet_keyboard(show_suggested_replies=False)
    on_kb = cabinet_keyboard(show_suggested_replies=True)
    off_labels = [btn.text for row in off_kb.inline_keyboard for btn in row]
    on_labels = [btn.text for row in on_kb.inline_keyboard for btn in row]
    assert msg.TXT_SUGGESTED_REPLIES_OFF in off_labels
    assert msg.TXT_SUGGESTED_REPLIES_ON in on_labels
    assert msg.CB_TOGGLE_SUGGESTED_REPLIES == "toggle_suggested_replies"
