"""Юнит-тесты без Telegram: ref из /start, черновик invoice, текст кабинета."""

from __future__ import annotations

import pytest

from config import Settings
from services import payments_catalog as paycat
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.start_turn import StartFlowOutcome, parse_telegram_start_ref, run_start_turn
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
    assert r.outcome is StartFlowOutcome.NEED_TERMS
    await repo_module.set_user_accepted_terms(uid)
    r2 = await run_start_turn(s, uid, None, "/start", is_subscribed=_subscribed)
    assert r2.outcome is StartFlowOutcome.WELCOME_MAIN_MENU


def test_parse_telegram_start_ref_none_and_empty() -> None:
    """Без аргумента или без ref — inviter не определяется."""
    assert parse_telegram_start_ref(None) is None
    assert parse_telegram_start_ref("/start") is None
    assert parse_telegram_start_ref("/start foo") is None


def test_parse_telegram_start_ref_valid() -> None:
    """Deep-link ``ref_<id>`` даёт числовой id пригласившего."""
    assert parse_telegram_start_ref("/start ref_42") == 42
    assert parse_telegram_start_ref("/start ref_0") == 0


def test_parse_telegram_start_ref_bad_suffix() -> None:
    assert parse_telegram_start_ref("/start ref_abc") is None


def test_build_payment_invoice_draft_ok_with_settings_override() -> None:
    """Черновик счёта: успех при карте и непустом токене провайдера."""
    s = Settings().model_copy(
        update={
            "payment_token": "test_provider_token",
            "shop_payment_title": "MyShop",
        }
    )
    r = build_payment_invoice_draft(s, user_id=7, pkg_index=1, method="r")
    assert r.outcome is InvoiceBuildOutcome.OK
    assert r.draft is not None
    assert r.draft.payload == paycat.build_invoice_payload(7, 1, "r")
    assert r.draft.currency == "RUB"
    assert r.draft.provider_token == "test_provider_token"
    assert "MyShop" in r.draft.title


def test_build_payment_invoice_draft_no_yookassa() -> None:
    """Карта без токена ЮKassa — отдельный исход для алерта в Telegram."""
    s = Settings().model_copy(update={"payment_token": ""})
    r = build_payment_invoice_draft(s, user_id=1, pkg_index=0, method="r")
    assert r.outcome is InvoiceBuildOutcome.NO_YOOKASSA


def test_build_payment_invoice_draft_invalid_method() -> None:
    s = Settings().model_copy(update={"payment_token": "x"})
    r = build_payment_invoice_draft(s, user_id=1, pkg_index=0, method="z")
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
    assert "👤 Мой профиль" in view.text
    assert "Твой ID:" in view.text
    assert "Текущий тариф: FREE" in view.text
    assert "Баланс: ⚡️ 37 | 💎 0" in view.text
    assert "CabinetTestBot" in view.text
    assert "ref_" in view.text


def test_resolve_tariff_shop_callback_back_and_pkg() -> None:
    """Навигация магазина: назад к списку пакетов и выбор пакета 0..2."""
    back = resolve_tariff_shop_callback(f"{paycat.CB_PAY_PKG_PREFIX}back")
    assert back.outcome is TariffShopNavOutcome.SHOP_INTRO
    assert back.text

    p1 = resolve_tariff_shop_callback(f"{paycat.CB_PAY_PKG_PREFIX}1")
    assert p1.outcome is TariffShopNavOutcome.CHOOSE_METHOD
    assert p1.pkg_index == 1
    assert p1.text

    bad = resolve_tariff_shop_callback("pk:99")
    assert bad.outcome is TariffShopNavOutcome.INVALID
