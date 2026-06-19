"""Юнит-тесты без Telegram: ref из /start, черновик invoice, текст кабинета."""

from __future__ import annotations

import pytest

from config import Settings
from services import payments_catalog as paycat
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.start_turn import StartFlowOutcome, parse_telegram_start_ref, run_start_turn
from platforms.tariffs_center import crystals_screen_for_tariff, tariffs_main_keyboard
from services.billing.types import TariffTier
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.tariff_shop_nav_turn import TariffShopNavOutcome, resolve_tariff_shop_callback


@pytest.mark.asyncio
async def test_new_user_must_accept_terms_before_start_menu(repo_module) -> None:
    uid = 99112
    await repo_module.ensure_user(uid)
    assert not await repo_module.user_has_accepted_terms(uid)
    s = Settings()

    async def _subscribed(_: int) -> bool:
        return True

    r = await run_start_turn(s, uid, None, "/start", is_subscribed=_subscribed)
    assert r.outcome is StartFlowOutcome.NEED_PAYWALL
    await repo_module.set_user_accepted_terms(uid)
    r2 = await run_start_turn(s, uid, None, "/start", is_subscribed=_subscribed)
    assert r2.outcome is StartFlowOutcome.WELCOME_MAIN_MENU


def test_parse_telegram_start_ref_none_and_empty() -> None:
    """Без аргумента или без ref — inviter не определяется."""
    assert parse_telegram_start_ref(None) is None
    assert parse_telegram_start_ref("/start") is None
    assert parse_telegram_start_ref("/start foo") is None


def test_parse_telegram_start_ref_valid() -> None:
    """Deep-link ``ref<id>`` и ``ref_<id>`` дают числовой id пригласившего."""
    assert parse_telegram_start_ref("/start ref42") == 42
    assert parse_telegram_start_ref("/start ref_42") == 42
    assert parse_telegram_start_ref("/start ref_0") == 0


def test_parse_telegram_start_ref_bad_suffix() -> None:
    assert parse_telegram_start_ref("/start ref_abc") is None


@pytest.mark.asyncio
async def test_build_payment_invoice_draft_ok_with_settings_override(repo_module) -> None:
    """Черновик счёта: успех при карте и непустом токене провайдера."""
    s = Settings().model_copy(
        update={
            "payment_token": "test_provider_token",
            "shop_payment_title": "MyShop",
        }
    )
    r = await build_payment_invoice_draft(s, user_id=7, pkg_index=1, method="r")
    assert r.outcome is InvoiceBuildOutcome.OK
    assert r.draft is not None
    assert r.draft.payload == paycat.build_invoice_payload(7, 1, "r")
    assert r.draft.currency == "RUB"
    assert r.draft.provider_token == "test_provider_token"
    assert "MyShop" in r.draft.title


@pytest.mark.asyncio
async def test_build_payment_invoice_draft_no_yookassa(repo_module) -> None:
    """Карта без токена ЮKassa — отдельный исход для алерта в Telegram."""
    s = Settings().model_copy(update={"payment_token": ""})
    r = await build_payment_invoice_draft(s, user_id=1, pkg_index=0, method="r")
    assert r.outcome is InvoiceBuildOutcome.NO_YOOKASSA


@pytest.mark.asyncio
async def test_build_payment_invoice_draft_invalid_method(repo_module) -> None:
    s = Settings().model_copy(update={"payment_token": "x"})
    r = await build_payment_invoice_draft(s, user_id=1, pkg_index=0, method="z")
    assert r.outcome is InvoiceBuildOutcome.INVALID


@pytest.mark.asyncio
async def test_build_cabinet_view_contains_user_and_ref(repo_module) -> None:
    """Текст кабинета собирается из БД и шаблона (изолированная БД)."""
    uid = 99010
    await repo_module.ensure_user(uid)
    await repo_module.update_balance(uid, "energy", 7)
    s = Settings().model_copy(update={"telegram_bot_username": "CabinetTestBot"})
    view = await build_cabinet_view(s, uid)
    assert str(uid) in view.text
    assert "Мой профиль NeuroMule" in view.text
    assert "Твой ID:" in view.text
    assert "<code>FREE</code>" in view.text
    assert "Баланс:" in view.text
    assert "CabinetTestBot" in view.text
    assert "start=ref99010" in view.text


def test_build_tariffs_entry_text_has_main_sections() -> None:
    text = build_tariffs_entry_text()
    assert "Магазин тарифов NeuroMule" in text
    assert "Совет дня" in text
    assert "Тариф FREE" in text
    assert "349 ₽" in text and "250 ⭐" in text
    assert "до 31%" in text
    assert "Полный разбор личности HD" in text  # SMART описание


def test_tariffs_main_keyboard_callbacks() -> None:
    kb = tariffs_main_keyboard()
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert "buy_bundle_menu" in data
    assert "buy_crystals_only_menu" in data
    assert "close_tariffs" in data


def test_crystals_menu_free_blocked() -> None:
    text, _kb = crystals_screen_for_tariff(TariffTier.FREE)
    assert "Доступ заблокирован" in text


def test_resolve_tariff_shop_callback_back_and_pkg() -> None:
    """Навигация магазина: назад к списку пакетов и выбор пакета 0..2."""
    back = resolve_tariff_shop_callback(f"{paycat.CB_PAY_PKG_PREFIX}back")
    assert back.outcome is TariffShopNavOutcome.SHOP_INTRO
    assert "Магазин тарифов NeuroMule" in back.text

    p1 = resolve_tariff_shop_callback(f"{paycat.CB_PAY_PKG_PREFIX}1")
    assert p1.outcome is TariffShopNavOutcome.CHOOSE_METHOD
    assert p1.pkg_index == 1
    assert p1.text

    bad = resolve_tariff_shop_callback("pk:99")
    assert bad.outcome is TariffShopNavOutcome.INVALID
