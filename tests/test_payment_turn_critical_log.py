"""PR-E: атомарность ``run_successful_payment_apply`` + CRITICAL логи.

Гарантии:

* Успешный платёж → SUCCESS, нет CRITICAL логов.
* Дубликат claim → DUPLICATE, нет CRITICAL логов.
* ``process_purchase.ok = False`` → INVALID + ровно один CRITICAL лог.
* Падение в ``process_purchase`` → проброс + CRITICAL с stacktrace.
* Падение в ``insert_payment_event`` → проброс + CRITICAL с stacktrace
  (кристаллы уже начислены — оператор должен либо удалить лишнее, либо
  вручную записать событие).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from services.use_cases import payment_turn as pt


@dataclass(frozen=True)
class _PurchaseStub:
    ok: bool = True
    energy_paid_added: int = 0
    crystals_added: int = 0
    tariff_updated: object | None = None


def _patch_common(
    mocker: MockerFixture,
    *,
    claim: bool = True,
    purchase: _PurchaseStub | None = None,
    purchase_exc: Exception | None = None,
    insert_event_exc: Exception | None = None,
) -> dict[str, object]:
    """Подменяет всё внешнее, чтобы изолировать логику use-case'а."""
    mocker.patch.object(
        pt.paycat,
        "parse_invoice_payload",
        return_value=(42, 0, "r"),
    )
    mocker.patch.object(pt, "pack_name_from_catalog_index", return_value="MINI")
    mocker.patch.object(
        pt.paycat,
        "PACKAGES",
        [SimpleNamespace(
            tariff="MINI", energy=100, crystals=20,
            rub_kopecks=29900, stars=200,
        )],
    )
    mocker.patch.object(
        pt, "claim_payment_charge", new=mocker.AsyncMock(return_value=claim)
    )
    mocker.patch.object(pt, "ensure_user", new=mocker.AsyncMock())

    purchase_mock = mocker.AsyncMock(
        return_value=purchase or _PurchaseStub(
            ok=True,
            energy_paid_added=100,
            crystals_added=20,
            tariff_updated=SimpleNamespace(value="MINI"),
        )
    )
    if purchase_exc is not None:
        purchase_mock.side_effect = purchase_exc
    mocker.patch.object(pt.billing, "process_purchase", new=purchase_mock)

    insert_mock = mocker.AsyncMock()
    if insert_event_exc is not None:
        insert_mock.side_effect = insert_event_exc
    mocker.patch.object(pt, "insert_payment_event", new=insert_mock)

    return {
        "purchase": purchase_mock,
        "insert_event": insert_mock,
    }


@pytest.mark.asyncio
async def test_successful_payment_no_critical_log(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_common(mocker)
    caplog.set_level(logging.CRITICAL, logger="services.use_cases.payment_turn")

    result = await pt.run_successful_payment_apply(
        42, "payload", "ch_abc", None, fallback_charge_id="fb"
    )

    assert result.outcome is pt.PaymentApplyOutcome.SUCCESS
    assert result.energy_credited == 100
    assert result.crystals_credited == 20
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL] == []


@pytest.mark.asyncio
async def test_duplicate_claim_returns_duplicate_no_critical(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_common(mocker, claim=False)
    caplog.set_level(logging.CRITICAL, logger="services.use_cases.payment_turn")

    result = await pt.run_successful_payment_apply(
        42, "payload", "ch_abc", None, fallback_charge_id="fb"
    )

    assert result.outcome is pt.PaymentApplyOutcome.DUPLICATE
    # На дубликате CRITICAL быть не должно — это штатная идемпотентность.
    assert [r for r in caplog.records if r.levelno == logging.CRITICAL] == []


@pytest.mark.asyncio
async def test_purchase_not_ok_logs_critical_and_returns_invalid(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_common(mocker, purchase=_PurchaseStub(ok=False))
    caplog.set_level(logging.CRITICAL, logger="services.use_cases.payment_turn")

    result = await pt.run_successful_payment_apply(
        42, "payload", "ch_abc", None, fallback_charge_id="fb"
    )

    assert result.outcome is pt.PaymentApplyOutcome.INVALID
    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticals) == 1
    msg = criticals[0].getMessage()
    assert "Payment failed for user 42" in msg
    assert "charge_id=ch_abc" in msg
    assert "MINI" in msg


@pytest.mark.asyncio
async def test_purchase_raises_logs_critical_with_stacktrace_and_reraises(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_common(mocker, purchase_exc=RuntimeError("db locked"))
    caplog.set_level(logging.CRITICAL, logger="services.use_cases.payment_turn")

    with pytest.raises(RuntimeError, match="db locked"):
        await pt.run_successful_payment_apply(
            42, "payload", "ch_abc", None, fallback_charge_id="fb"
        )

    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticals) == 1
    assert criticals[0].exc_info is not None  # stacktrace для пост-mortem'а
    assert "Payment failed for user 42" in criticals[0].getMessage()


@pytest.mark.asyncio
async def test_insert_event_raises_logs_critical_after_credit(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """Кристаллы уже начислены, а событие записать не удалось → CRITICAL."""
    mocks = _patch_common(mocker, insert_event_exc=RuntimeError("disk full"))
    caplog.set_level(logging.CRITICAL, logger="services.use_cases.payment_turn")

    with pytest.raises(RuntimeError, match="disk full"):
        await pt.run_successful_payment_apply(
            42, "payload", "ch_abc", None, fallback_charge_id="fb"
        )

    # process_purchase успел отработать — это видно по факту вызова.
    mocks["purchase"].assert_awaited_once()
    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticals) == 1
    assert criticals[0].exc_info is not None
    assert "post-claim step crashed" in criticals[0].getMessage()
