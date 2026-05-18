"""Use-case: успешная оплата Telegram invoice — идемпотентное начисление кристаллов."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from services import payments_catalog as paycat
from config import settings
from services.repository import (
    claim_payment_charge,
    ensure_user,
    insert_payment_event,
    mark_user_first_purchase_and_get_referrer,
    set_user_tariff,
    update_balance,
)


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
    """
    Проверяет payload, charge id, начисляет кристаллы один раз на charge.

    Вход:
        payer_telegram_id — ``message.from_user.id`` из Telegram.
        invoice_payload — ``successful_payment.invoice_payload``.
        telegram_charge_id / provider_charge_id — идентификаторы платежа.
        fallback_charge_id — если оба id пусты (редко), уникальная строка снаружи, например ``msg:chat:id``.

    Возвращает:
        ``PaymentApplyResult`` с исходом и объёмом начисления при успехе.
    """
    parsed = paycat.parse_invoice_payload(invoice_payload or "")
    if not parsed:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)
    uid, pkg_i, _method = parsed
    if uid != payer_telegram_id:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    pack = paycat.PACKAGES[pkg_i]
    energy = pack.energy
    crystals = pack.crystals
    charge_id = (telegram_charge_id or provider_charge_id or "").strip()
    if not charge_id:
        charge_id = (fallback_charge_id or "").strip()
    if not charge_id:
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    if not await claim_payment_charge(charge_id, uid, energy + crystals):
        return PaymentApplyResult(outcome=PaymentApplyOutcome.DUPLICATE)

    await ensure_user(uid)
    if energy:
        await update_balance(uid, "energy", energy)
    if crystals:
        await update_balance(uid, "crystals", crystals)
    if pack.is_tariff:
        await set_user_tariff(uid, pack.tariff)
    amount = pack.rub_kopecks if _method == "r" else pack.stars
    currency = "RUB" if _method == "r" else "XTR"
    await insert_payment_event(uid, pack.tariff, _method, amount, currency)
    inviter_id = await mark_user_first_purchase_and_get_referrer(uid)
    if inviter_id is not None and inviter_id > 0:
        await ensure_user(inviter_id)
        await update_balance(inviter_id, "crystals", settings.referral_bonus_energy)
    return PaymentApplyResult(
        outcome=PaymentApplyOutcome.SUCCESS,
        energy_credited=energy,
        crystals_credited=crystals,
        tariff_activated=pack.tariff,
    )
