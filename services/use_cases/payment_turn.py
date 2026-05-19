"""Use-case: успешная оплата Telegram invoice — идемпотентное начисление через BillingManager."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from services import payments_catalog as paycat
from services.billing import billing
from services.billing.shop import pack_name_from_catalog_index
from services.repository import claim_payment_charge, ensure_user, insert_payment_event


class PaymentApplyOutcome(str, Enum):
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    SUCCESS = "success"


@dataclass(frozen=True)
class PaymentApplyResult:
    """Результат ``run_successful_payment_apply``."""

    outcome: PaymentApplyOutcome
    energy_credited: int = 0
    crystals_credited: int = 0
    tariff_activated: str = ""


async def run_successful_payment_apply(
    payer_telegram_id: int,
    invoice_payload: str,
    telegram_charge_id: str | None,
    provider_charge_id: str | None,
    *,
    fallback_charge_id: str | None = None,
) -> PaymentApplyResult:
    parsed = paycat.parse_invoice_payload(invoice_payload or "")
    if not parsed:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)
    uid, pkg_i, _method = parsed
    if uid != payer_telegram_id:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    pack_name = pack_name_from_catalog_index(pkg_i)
    if not pack_name:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    pack = paycat.PACKAGES[pkg_i]
    charge_id = (telegram_charge_id or provider_charge_id or "").strip()
    if not charge_id:
        charge_id = (fallback_charge_id or "").strip()
    if not charge_id:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    energy = int(pack.energy)
    crystals = int(pack.crystals)
    if not await claim_payment_charge(charge_id, uid, energy + crystals):
        return PaymentApplyResult(outcome=PaymentApplyOutcome.DUPLICATE)

    await ensure_user(uid)
    purchase = await billing.process_purchase(uid, pack_name)
    if not purchase.ok:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    amount = pack.rub_kopecks if _method == "r" else pack.stars
    currency = "RUB" if _method == "r" else "XTR"
    await insert_payment_event(uid, pack.tariff, _method, amount, currency)

    tariff_label = purchase.tariff_updated.value if purchase.tariff_updated else pack.tariff
    return PaymentApplyResult(
        outcome=PaymentApplyOutcome.SUCCESS,
        energy_credited=purchase.energy_paid_added,
        crystals_credited=purchase.crystals_added,
        tariff_activated=tariff_label,
    )
