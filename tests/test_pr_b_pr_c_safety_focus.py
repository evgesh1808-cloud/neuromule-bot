"""Фокус-тесты безопасности PR-B и PR-C через ``pytest-mock``.

Эти тесты — целенаправленные «контрактные» гаранты:

* PR-C — ``download_telegram_document_to_buffer``:
    - over-limit ``file_size`` → ``DocumentTooBigError`` ДО любого I/O;
    - ``file_size is None`` → скачиваем и валидируем размер ПОСЛЕ.

* PR-B — ``is_stars_insufficient_balance`` + интеграция в
  ``payment_misc.pay_pick_method``:
    - "NETWORK_ERROR" → False (не маркер) → хинт не показан;
    - "PROVIDER_TOKEN_INVALID" → False (не маркер) → хинт не показан;
    - ``method == "r"`` (карта) → хинт не показан, даже если ошибка =
      whitelist-маркер (рекламировать карту бессмысленно);
    - успешный инвойс (без exception) → хинт не показан.

`bot` и `callback`/`message` мокируются через ``mocker.MagicMock`` /
``mocker.AsyncMock`` — без поднятия aiogram-Dispatcher'а и БД.
"""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from pytest_mock import MockerFixture

from services import file_processor as fp
from services.billing.stars_payment_hints import is_stars_insufficient_balance


# ─────────────────────────────────────────────────────────────────────────
# PR-C · download_telegram_document_to_buffer (15 МБ guard)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pr_c_raises_when_file_size_exceeds_limit(mocker: MockerFixture) -> None:
    """over-limit detected ДО I/O: get_file / download_file НЕ вызываются."""
    bot = mocker.MagicMock()
    bot.get_file = mocker.AsyncMock()
    bot.download_file = mocker.AsyncMock()
    document = SimpleNamespace(
        file_id="big_pdf_id",
        file_size=fp.MAX_DOCUMENT_BYTES + 1,
        file_name="big.pdf",
    )

    with pytest.raises(fp.DocumentTooBigError) as excinfo:
        await fp.download_telegram_document_to_buffer(bot, document)

    assert excinfo.value.size_bytes == fp.MAX_DOCUMENT_BYTES + 1
    assert excinfo.value.limit_bytes == fp.MAX_DOCUMENT_BYTES
    # Никаких сетевых вызовов — экономим трафик ноды.
    bot.get_file.assert_not_called()
    bot.download_file.assert_not_called()


@pytest.mark.asyncio
async def test_pr_c_unknown_size_triggers_post_download_validation(
    mocker: MockerFixture,
) -> None:
    """file_size=None → скачиваем, но потом ловим over-limit по факту."""
    bot = mocker.MagicMock()
    bot.get_file = mocker.AsyncMock(
        return_value=SimpleNamespace(file_path="documents/forwarded.pdf")
    )

    too_big_payload = b"X" * (fp.MAX_DOCUMENT_BYTES + 1)

    async def _download(file_path: str, destination: BytesIO) -> None:
        destination.write(too_big_payload)

    bot.download_file = mocker.AsyncMock(side_effect=_download)
    document = SimpleNamespace(file_id="forwarded_id", file_size=None, file_name="notes.txt")

    with pytest.raises(fp.DocumentTooBigError) as excinfo:
        await fp.download_telegram_document_to_buffer(bot, document)

    assert excinfo.value.size_bytes == fp.MAX_DOCUMENT_BYTES + 1
    assert excinfo.value.limit_bytes == fp.MAX_DOCUMENT_BYTES
    # На этот раз download_file ОЖИДАЕТСЯ вызванным (file_size был unknown).
    bot.get_file.assert_awaited_once_with("forwarded_id")
    bot.download_file.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────
# PR-B · is_stars_insufficient_balance: 4 негативных кейса
# ─────────────────────────────────────────────────────────────────────────


def test_pr_b_negative_network_error() -> None:
    """1/4: 'NETWORK_ERROR' — НЕ маркер нехватки Stars."""
    assert is_stars_insufficient_balance("NETWORK_ERROR") is False
    assert is_stars_insufficient_balance("Bad Request: NETWORK_ERROR") is False


def test_pr_b_negative_provider_token_invalid() -> None:
    """2/4: 'PROVIDER_TOKEN_INVALID' — провайдер отключён, не Stars-баланс."""
    assert is_stars_insufficient_balance("PROVIDER_TOKEN_INVALID") is False
    assert (
        is_stars_insufficient_balance("Bad Request: PROVIDER_TOKEN_INVALID")
        is False
    )


# Сценарии 3/4 и 4/4 — интеграционные: проверяем, что НИКАКОЙ хинт не
# уходит юзеру при method='r' и при успешном инвойсе.


@pytest.fixture
def _patch_invoice_builder(mocker: MockerFixture):
    """Подменяет ``payment_shop.create_*_invoice``, чтобы не зависеть от каталога."""
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

    mocker.patch.object(
        payment_misc.payment_shop,
        "create_telegram_stars_invoice",
        new=mocker.AsyncMock(return_value=result_ok),
    )
    mocker.patch.object(
        payment_misc.payment_shop,
        "create_yookassa_invoice",
        new=mocker.AsyncMock(return_value=result_ok),
    )
    return payment_misc


def _make_callback(mocker: MockerFixture, method: str) -> object:
    """CallbackQuery-стаб; формат data строго по regex pm:N:[rx]."""
    cb = mocker.MagicMock()
    cb.from_user = SimpleNamespace(id=42)
    cb.data = f"pm:0:{method}"
    cb.answer = mocker.AsyncMock()
    cb.message = mocker.MagicMock()
    cb.message.answer_invoice = mocker.AsyncMock()
    cb.message.answer = mocker.AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_pr_b_negative_rub_method_does_not_show_hint(
    _patch_invoice_builder, mocker: MockerFixture
) -> None:
    """3/4: method='r' (карта/RUB) → хинт не нужен даже при whitelist-маркере.

    Рекламировать карту тому, кто уже платит картой — бессмысленно."""
    cb = _make_callback(mocker, method="r")
    cb.message.answer_invoice.side_effect = TelegramBadRequest(
        method=mocker.MagicMock(), message="Bad Request: BALANCE_TOO_LOW"
    )
    await _patch_invoice_builder.pay_pick_method(cb)
    cb.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_b_negative_successful_invoice_does_not_show_hint(
    _patch_invoice_builder, mocker: MockerFixture
) -> None:
    """4/4: Stars-инвойс выставлен без исключения → хинт не показывается."""
    cb = _make_callback(mocker, method="x")
    # answer_invoice не бросает — это успешный путь.
    await _patch_invoice_builder.pay_pick_method(cb)
    cb.message.answer_invoice.assert_awaited_once()
    cb.message.answer.assert_not_awaited()


# Доп. контрольный позитивный кейс — гарантия, что детектор НЕ сломан:
# при правильном маркере на Stars хинт всё-таки приходит.


@pytest.mark.asyncio
async def test_pr_b_positive_control_stars_low_balance_does_show_hint(
    _patch_invoice_builder, mocker: MockerFixture
) -> None:
    """Контрольный позитив: method='x' + BALANCE_TOO_LOW → хинт показан."""
    cb = _make_callback(mocker, method="x")
    cb.message.answer_invoice.side_effect = TelegramBadRequest(
        method=mocker.MagicMock(), message="Bad Request: BALANCE_TOO_LOW"
    )
    await _patch_invoice_builder.pay_pick_method(cb)
    cb.message.answer.assert_awaited_once()
    _, kwargs = cb.message.answer.call_args
    assert kwargs.get("parse_mode") == "HTML"
