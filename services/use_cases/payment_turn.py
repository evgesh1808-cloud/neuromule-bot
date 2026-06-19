"""Use-case: успешная оплата Telegram invoice — идемпотентное начисление.

Production-инвариант (PR-E): любой сбой пост-claim шага логируется как
``CRITICAL: Payment failed for user {user_id} …`` с полным контекстом
(charge_id, pack, ошибка). Это сигнал для ручного saga-compensation:
деньги уже списаны, но товар не выдан — оператору видно в логах по
``CRITICAL`` уровню.

Порядок шагов (важно):
1. ``claim_payment_charge`` — атомарная идемпотентность first-write-wins.
   При retry от Telegram'а (типично для Stars) этот шаг вернёт ``False``,
   и мы выдадим ``DUPLICATE`` без двойного начисления.
2. Внутри ``try`` — фактическое начисление и запись события.
3. На ЛЮБОЙ ошибке после claim → ``logger.critical`` + проброс. Хэндлер
   выше отвечает юзеру про обращение в поддержку, оператор разбирает
   ситуацию вручную по charge_id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from services import metrics, payments_catalog as paycat
from services.billing import billing
from services.billing.shop import pack_name_from_catalog_index
from services.repository import (
    claim_payment_charge,
    ensure_user,
    insert_payment_event,
)

logger = logging.getLogger(__name__)


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
        metrics.incr("payment.invalid", {"reason": "bad_payload"})
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)
    uid, pkg_i, _method = parsed
    if uid != payer_telegram_id:
        metrics.incr("payment.invalid", {"reason": "uid_mismatch"})
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    pack_name = pack_name_from_catalog_index(pkg_i)
    if not pack_name:
        metrics.incr("payment.invalid", {"reason": "unknown_pack"})
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    pack = paycat.PACKAGES[pkg_i]
    charge_id = (telegram_charge_id or provider_charge_id or "").strip()
    if not charge_id:
        charge_id = (fallback_charge_id or "").strip()
    if not charge_id:
        metrics.incr("payment.invalid", {"reason": "no_charge_id"})
        return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

    energy = int(pack.energy)
    crystals = int(pack.crystals)
    if not await claim_payment_charge(charge_id, uid, energy + crystals):
        metrics.incr("payment.duplicate")
        return PaymentApplyResult(outcome=PaymentApplyOutcome.DUPLICATE)

    # ── Атомарная зона (PR-E): любое падение здесь = CRITICAL + проброс ──
    # claim уже сделан, повторная обработка Telegram'ом вернёт DUPLICATE,
    # поэтому неудача начисления должна попасть в манульный разбор.
    try:
        await ensure_user(uid)
        purchase = await billing.process_purchase(uid, pack_name)
        if not purchase.ok:
            metrics.incr("payment.failed", {"reason": "process_purchase_not_ok"})
            logger.critical(
                "Payment failed for user %s (process_purchase returned not_ok) "
                "charge_id=%s pack=%s — manual saga compensation required",
                uid,
                charge_id,
                pack_name,
            )
            return PaymentApplyResult(outcome=PaymentApplyOutcome.INVALID)

        amount = pack.rub_kopecks if _method == "r" else pack.stars
        currency = "RUB" if _method == "r" else "XTR"
        await insert_payment_event(uid, pack.tariff, _method, amount, currency)
    except Exception:
        metrics.incr("payment.failed", {"reason": "post_claim_exception"})
        # Деньги списаны, claim зафиксирован, но шаг начисления / записи
        # события упал. Это саппорт-тикет: оператор должен либо вручную
        # начислить пакет, либо вернуть платёж. CRITICAL уровень — чтобы
        # сразу попало в алёрты SRE.
        logger.critical(
            "Payment failed for user %s charge_id=%s pack=%s — "
            "post-claim step crashed, manual saga compensation required",
            uid,
            charge_id,
            pack_name,
            exc_info=True,
        )
        raise

    tariff_label = (
        purchase.tariff_updated.value if purchase.tariff_updated else pack.tariff
    )
    metrics.incr("payment.success", {"method": _method, "pack": pack_name})
    return PaymentApplyResult(
        outcome=PaymentApplyOutcome.SUCCESS,
        energy_credited=purchase.energy_paid_added,
        crystals_credited=purchase.crystals_added,
        tariff_activated=tariff_label,
    )
