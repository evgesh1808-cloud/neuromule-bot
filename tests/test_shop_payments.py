"""Платежи: shop.py напрямую (ЮKassa webhook + Telegram Stars)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from services.billing import shop as payment_shop
from services.billing.shop import PaymentOutcome


@pytest.mark.asyncio
async def test_yookassa_payment_succeeded_fulfills_and_referral(
    mocker: MockerFixture,
) -> None:
    grant = mocker.patch.object(
        payment_shop.store,
        "grant_balance_package",
        new=AsyncMock(return_value=1),
    )
    mocker.patch.object(
        payment_shop.store,
        "init_billing_schema",
        new=AsyncMock(),
    )
    mocker.patch.object(
        payment_shop,
        "activate_duo_owner",
        new=AsyncMock(),
    )
    mocker.patch.object(
        payment_shop,
        "set_user_tariff",
        new=AsyncMock(),
    )
    mocker.patch.object(
        payment_shop.store,
        "mark_first_purchase_done",
        new=AsyncMock(return_value=9001),
    )
    mocker.patch.object(
        payment_shop,
        "claim_payment_charge",
        new=AsyncMock(return_value=True),
    )
    mocker.patch.object(payment_shop, "ensure_user", new=AsyncMock())
    mocker.patch.object(payment_shop, "insert_payment_event", new=AsyncMock())

    body = {
        "event": "payment.succeeded",
        "object": {
            "id": "yk_charge_abc",
            "save_payment_method": True,
            "payment_method": {"saved": True},
            "metadata": {"user_id": "42", "pack_name": "MINI"},
        },
    }
    result = await payment_shop.handle_yookassa_webhook(body)

    assert result.outcome is PaymentOutcome.SUCCESS
    assert result.save_payment_method is True
    assert result.energy_credited == payment_shop.SHOP_PACKS["MINI"]["energy_paid"]
    assert result.crystals_credited == payment_shop.SHOP_PACKS["MINI"]["crystals"]
    assert result.referral_crystals_to_inviter == payment_shop.REFERRAL_FIRST_PURCHASE_CRYSTALS
    assert grant.await_count == 2
    grant.assert_any_call(
        42,
        kind="MINI",
        energy_amount=int(payment_shop.SHOP_PACKS["MINI"]["energy_paid"]),
        crystals_amount=int(payment_shop.SHOP_PACKS["MINI"]["crystals"]),
        expires_at=mocker.ANY,
    )
    grant.assert_any_call(
        9001,
        kind="referral_first_purchase",
        energy_amount=0,
        crystals_amount=payment_shop.REFERRAL_FIRST_PURCHASE_CRYSTALS,
        expires_at=None,
    )


@pytest.mark.asyncio
async def test_yookassa_save_payment_method_from_object_flag(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(payment_shop.store, "grant_balance_package", new=AsyncMock(return_value=1))
    mocker.patch.object(payment_shop.store, "init_billing_schema", new=AsyncMock())
    mocker.patch.object(payment_shop.store, "mark_first_purchase_done", new=AsyncMock(return_value=None))
    mocker.patch.object(payment_shop, "claim_payment_charge", new=AsyncMock(return_value=True))
    mocker.patch.object(payment_shop, "ensure_user", new=AsyncMock())
    mocker.patch.object(payment_shop, "insert_payment_event", new=AsyncMock())
    mocker.patch.object(payment_shop, "set_user_tariff", new=AsyncMock())

    body = {
        "event": "payment.succeeded",
        "object": {
            "id": "yk_saved_card",
            "payment_method": {"saved": True},
            "metadata": {"user_id": "7", "pack_name": "crystals_10"},
        },
    }
    result = await payment_shop.handle_yookassa_webhook(body)
    assert result.outcome is PaymentOutcome.SUCCESS
    assert result.save_payment_method is True


@pytest.mark.asyncio
async def test_yookassa_ignored_for_non_succeeded_event() -> None:
    result = await payment_shop.handle_yookassa_webhook({"event": "payment.canceled", "object": {}})
    assert result.outcome is PaymentOutcome.IGNORED


def test_pre_checkout_valid_when_pack_in_shop_packs() -> None:
    payload = payment_shop.paycat.build_invoice_payload(100, 0, "x")
    assert payment_shop.validate_pre_checkout_payload(payload, 100) is True


def test_pre_checkout_rejects_unknown_pack_index() -> None:
    payload = "nm:100:99:x"
    assert payment_shop.validate_pre_checkout_payload(payload, 100) is False


def test_pre_checkout_rejects_uid_mismatch() -> None:
    payload = payment_shop.paycat.build_invoice_payload(100, 0, "x")
    assert payment_shop.validate_pre_checkout_payload(payload, 999) is False


@pytest.mark.asyncio
async def test_telegram_stars_successful_payment_chain(mocker: MockerFixture) -> None:
    grant = mocker.patch.object(
        payment_shop.store,
        "grant_balance_package",
        new=AsyncMock(return_value=1),
    )
    mocker.patch.object(payment_shop.store, "init_billing_schema", new=AsyncMock())
    mocker.patch.object(payment_shop.store, "mark_first_purchase_done", new=AsyncMock(return_value=None))
    mocker.patch.object(payment_shop, "claim_payment_charge", new=AsyncMock(return_value=True))
    mocker.patch.object(payment_shop, "ensure_user", new=AsyncMock())
    mocker.patch.object(payment_shop, "insert_payment_event", new=AsyncMock())
    mocker.patch.object(payment_shop, "set_user_tariff", new=AsyncMock())

    payload = payment_shop.paycat.build_invoice_payload(55, 1, "x")
    result = await payment_shop.handle_telegram_stars_payment(
        55,
        payload,
        "tg_charge_stars_1",
        None,
    )
    assert result.outcome is PaymentOutcome.SUCCESS
    assert result.charge_id == "tg_charge_stars_1"
    grant.assert_called_once()
    grant.assert_called_with(
        55,
        kind="SMART",
        energy_amount=int(payment_shop.SHOP_PACKS["SMART"]["energy_paid"]),
        crystals_amount=int(payment_shop.SHOP_PACKS["SMART"]["crystals"]),
        expires_at=mocker.ANY,
    )


@pytest.mark.asyncio
async def test_telegram_stars_duplicate_claim(mocker: MockerFixture) -> None:
    mocker.patch.object(payment_shop, "claim_payment_charge", new=AsyncMock(return_value=False))
    payload = payment_shop.paycat.build_invoice_payload(1, 0, "x")
    result = await payment_shop.handle_telegram_stars_payment(1, payload, "dup_id", None)
    assert result.outcome is PaymentOutcome.DUPLICATE


@pytest.mark.asyncio
async def test_process_purchase_ultra_activates_duo_owner(
    repo_module,
    mocker: MockerFixture,
) -> None:
    uid = 88001
    inviter = 88000
    await repo_module.ensure_user(uid)
    await repo_module.ensure_user(inviter)
    async with __import__("aiosqlite").connect(repo_module.DB_PATH) as db:
        await db.execute("UPDATE users SET referred_by = ? WHERE id = ?", (inviter, uid))
        await db.commit()

    activate = mocker.spy(payment_shop, "activate_duo_owner")
    result = await payment_shop.process_purchase(uid, "ULTRA")
    assert result.ok
    activate.assert_called_once_with(uid)

    inviter_state = await payment_shop.store.load_user_billing(inviter)
    assert inviter_state.crystals >= payment_shop.REFERRAL_FIRST_PURCHASE_CRYSTALS


@pytest.mark.asyncio
async def test_create_yookassa_invoice_direct_api_save_payment_method(
    mocker: MockerFixture,
) -> None:
    from config import Settings

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "confirmation": {"confirmation_url": "https://yookassa.ru/pay/test"},
    }
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("services.billing.shop.httpx.AsyncClient", return_value=mock_client)

    cfg = Settings(
        tg_token="x",
        openrouter_key="y",
        gemini_api_key="z",
        yookassa_shop_id="shop123",
        yookassa_secret_key="secret456",
    )
    inv = await payment_shop.create_yookassa_invoice(cfg, 42, 0)
    assert inv.outcome is payment_shop.InvoiceBuildOutcome.OK
    assert inv.draft is not None
    assert inv.draft.save_payment_method is True
    assert inv.draft.confirmation_url.startswith("https://")
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["save_payment_method"] is True


@pytest.mark.asyncio
async def test_fulfill_critical_on_process_failure(
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mocker.patch.object(payment_shop, "claim_payment_charge", new=AsyncMock(return_value=True))
    mocker.patch.object(payment_shop, "ensure_user", new=AsyncMock())
    mocker.patch.object(
        payment_shop,
        "process_purchase",
        new=AsyncMock(return_value=SimpleNamespace(ok=False)),
    )
    caplog.set_level(logging.CRITICAL, logger="services.billing.shop")
    result = await payment_shop.fulfill_payment(1, "MINI", "ch_fail", "r")
    assert result.outcome is PaymentOutcome.INVALID
    assert any("Payment failed" in r.message for r in caplog.records)
