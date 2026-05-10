"""
Use-case: параметры счёта Telegram (invoice) после выбора пакета и способа оплаты.

Без вызова Bot API — только данные для ``answer_invoice``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from config import Settings
from services import payments_catalog as paycat


class InvoiceBuildOutcome(str, Enum):
    """Итог подготовки счёта."""

    OK = "ok"
    NO_YOOKASSA = "no_yookassa"
    INVALID = "invalid"


@dataclass(frozen=True)
class InvoicePriceLine:
    """Одна строка в ``prices`` (как у ``LabeledPrice``)."""

    label: str
    amount: int


@dataclass(frozen=True)
class PaymentInvoiceDraft:
    """Готовые поля для ``answer_invoice``."""

    title: str
    description: str
    payload: str
    currency: str
    prices: tuple[InvoicePriceLine, ...]
    provider_token: str


@dataclass(frozen=True)
class BuildPaymentInvoiceResult:
    outcome: InvoiceBuildOutcome
    draft: PaymentInvoiceDraft | None = None


def build_payment_invoice_draft(
    settings: Settings,
    user_id: int,
    pkg_index: int,
    method: str,
) -> BuildPaymentInvoiceResult:
    """
    Собирает payload, валюту, цены и подписи для Telegram Payments.

    Вход:
        settings — конфиг (название магазина, токен ЮKassa).
        user_id — покупатель (Telegram id).
        pkg_index — индекс пакета ``0..2``.
        method — ``r`` (карта/RUB) или ``x`` (Stars).

    Возвращает:
        ``BuildPaymentInvoiceResult``; при ``OK`` заполнен ``draft``.
    """
    if method not in ("r", "x") or pkg_index not in (0, 1, 2):
        return BuildPaymentInvoiceResult(outcome=InvoiceBuildOutcome.INVALID)
    if method == "r" and not settings.payment_token.strip():
        return BuildPaymentInvoiceResult(outcome=InvoiceBuildOutcome.NO_YOOKASSA)

    pack = paycat.PACKAGES[pkg_index]
    payload = paycat.build_invoice_payload(user_id, pkg_index, method)
    currency = paycat.invoice_currency(method)
    ptoken = paycat.provider_token_for(method, settings.payment_token.strip())
    title = f"{pack.tariff} · {settings.shop_payment_title}"[:32]
    description = f"Тариф {pack.tariff}: +{pack.energy} ⚡"
    raw_prices = paycat.labeled_prices_for(pack, method)
    prices = tuple(InvoicePriceLine(label=lp.label, amount=lp.amount) for lp in raw_prices)
    draft = PaymentInvoiceDraft(
        title=title,
        description=description[:255],
        payload=payload,
        currency=currency,
        prices=prices,
        provider_token=ptoken,
    )
    return BuildPaymentInvoiceResult(outcome=InvoiceBuildOutcome.OK, draft=draft)
