"""PR-B: детектор «Stars insufficient balance» + интеграция в payment_misc.

Критическая зона. Тесты гарантируют:

1. ``is_stars_insufficient_balance`` — strict whitelist:
   - срабатывает на ``BALANCE_TOO_LOW`` / ``INSUFFICIENT_BALANCE`` /
     ``PAYMENT_REQUIRES_TOPUP`` (различный регистр);
   - НЕ срабатывает на сетевых сбоях, отключённом провайдере, общих 400.
2. ``pay_pick_method`` показывает HTML-хинт ТОЛЬКО при:
   - ``method == 'x'`` (Stars-инвойс);
   - тексте ошибки, содержащем whitelist-маркер.
3. ``pay_pick_method`` НЕ показывает хинт при:
   - ``method == 'r'`` (карта/RUB) — никаких ложных срабатываний;
   - любом другом тексте BadRequest (network, provider).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from services.billing.stars_payment_hints import (
    INSUFFICIENT_STARS_MARKERS,
    is_stars_insufficient_balance,
)


# ──────────────────────────────────────────────────────────────────
# Unit: strict whitelist detector
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "BALANCE_TOO_LOW",
        "Bad Request: BALANCE_TOO_LOW",
        "balance_too_low (insufficient stars)",
        "INSUFFICIENT_BALANCE: please top up",
        "PAYMENT_REQUIRES_TOPUP",
        "STARS_BALANCE_TOO_LOW for user 42",
    ],
)
def test_detector_returns_true_on_whitelist(text: str) -> None:
    assert is_stars_insufficient_balance(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "network is unreachable",
        "Bad Request: BOT_PAYMENTS_DISABLED",
        "PROVIDER_TOKEN_INVALID",
        "Connection reset by peer",
        "Forbidden: bot was blocked by the user",
        "Too Many Requests: retry after 5",
        "internal server error",
    ],
)
def test_detector_returns_false_on_non_whitelist(text: str | None) -> None:
    assert is_stars_insufficient_balance(text) is False


def test_whitelist_is_uppercased() -> None:
    """Защита от регрессии: маркеры должны лежать в UPPER (так как
    haystack приводится к UPPER перед сравнением)."""
    assert all(m == m.upper() for m in INSUFFICIENT_STARS_MARKERS)


# ──────────────────────────────────────────────────────────────────
# Integration: pay_pick_method — хинт только при Stars + whitelist
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def _payment_test_env(monkeypatch: pytest.MonkeyPatch):
    """Изоляция от реального aiogram-роутера и БД: моки shop API и keyboards."""
    from platforms.handlers import payment_misc
    from services.billing.shop import (
        InvoiceBuildOutcome,
        InvoiceBuildResult,
        InvoicePriceLine,
        PaymentInvoiceDraft,
    )

    draft = PaymentInvoiceDraft(
        title="t",
        description="d",
        payload="p",
        currency="XTR",
        prices=(InvoicePriceLine(label="L", amount=10),),
        provider_token="",
    )
    result_ok = InvoiceBuildResult(outcome=InvoiceBuildOutcome.OK, draft=draft)

    async def _stars_invoice(*_a, **_kw):
        return result_ok

    async def _yookassa_invoice(*_a, **_kw):
        return result_ok

    monkeypatch.setattr(
        payment_misc.payment_shop,
        "create_telegram_stars_invoice",
        _stars_invoice,
    )
    monkeypatch.setattr(
        payment_misc.payment_shop,
        "create_yookassa_invoice",
        _yookassa_invoice,
    )
    return payment_misc


def _make_callback(method: str, pkg_index: int = 0, uid: int = 42) -> MagicMock:
    """Сконструировать CallbackQuery-стаб с .message.answer_invoice / .answer.

    Формат callback.data строго совпадает с регуляркой
    ``services/payments_catalog._RE_METHOD = ^pm:(\\d+):([rx])$``.
    """
    cb = MagicMock()
    cb.from_user = SimpleNamespace(id=uid)
    cb.data = f"pm:{pkg_index}:{method}"
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer_invoice = AsyncMock()
    cb.message.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_no_hint_on_successful_stars_invoice(_payment_test_env, monkeypatch) -> None:
    """Stars-инвойс выставлен ОК → хинт не нужен."""
    cb = _make_callback("x")
    await _payment_test_env.pay_pick_method(cb)
    cb.message.answer_invoice.assert_awaited_once()
    cb.message.answer.assert_not_awaited()  # никакого хинта


@pytest.mark.asyncio
async def test_hint_shown_on_stars_insufficient_balance(_payment_test_env) -> None:
    """Stars + whitelist-маркер → HTML-хинт показан ровно один раз."""
    cb = _make_callback("x")
    cb.message.answer_invoice.side_effect = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: BALANCE_TOO_LOW"
    )
    await _payment_test_env.pay_pick_method(cb)

    cb.message.answer_invoice.assert_awaited_once()
    cb.message.answer.assert_awaited_once()
    _, kwargs = cb.message.answer.call_args
    assert kwargs.get("parse_mode") == "HTML"
    # И сам текст — про карту:
    args = cb.message.answer.call_args.args
    assert "40%" in args[0]


@pytest.mark.asyncio
async def test_no_hint_for_rub_method_even_on_balance_error(_payment_test_env) -> None:
    """method='r' (карта/RUB) — рекламировать карту бессмысленно."""
    cb = _make_callback("r")
    cb.message.answer_invoice.side_effect = TelegramBadRequest(
        method=MagicMock(), message="BALANCE_TOO_LOW"
    )
    await _payment_test_env.pay_pick_method(cb)
    cb.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_hint_on_network_error_in_stars(_payment_test_env) -> None:
    """method='x' но текст ошибки НЕ из whitelist → хинт НЕ показан.

    Это основная анти-фейк защита: при сетевом сбое мы не должны
    предлагать карту, потому что юзер мог иметь достаточно Stars."""
    cb = _make_callback("x")
    cb.message.answer_invoice.side_effect = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: network temporarily unavailable"
    )
    await _payment_test_env.pay_pick_method(cb)
    cb.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_hint_on_provider_token_invalid(_payment_test_env) -> None:
    """Отключённый провайдер не должен путаться с нехваткой Stars."""
    cb = _make_callback("x")
    cb.message.answer_invoice.side_effect = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: PROVIDER_TOKEN_INVALID"
    )
    await _payment_test_env.pay_pick_method(cb)
    cb.message.answer.assert_not_awaited()
