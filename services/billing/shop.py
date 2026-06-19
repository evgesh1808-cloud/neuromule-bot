"""Магазин: начисления, инвойсы и обработка успешных платежей (ЮKassa / Stars)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any

import httpx

from config import Settings, settings
from services import metrics, payments_catalog as paycat
from services.billing import store
from services.billing.pricing import REFERRAL_FIRST_PURCHASE_CRYSTALS, SHOP_PACKS
from services.billing.types import PurchaseResult, ShopPackName, TariffTier
from services.family_sharing import activate_duo_owner
from services.repository import claim_payment_charge, ensure_user, insert_payment_event, set_user_tariff

logger = logging.getLogger(__name__)

_PACK_ALIASES: dict[str, str] = {
    "mini": ShopPackName.MINI.value,
    "smart": ShopPackName.SMART.value,
    "ultra": ShopPackName.ULTRA_1MONTH.value,
    "ultra_3days": ShopPackName.ULTRA_3DAYS.value,
    "ultra_1week": ShopPackName.ULTRA_1WEEK.value,
    "ultra_1month": ShopPackName.ULTRA_1MONTH.value,
    "10": ShopPackName.CRYSTALS_10.value,
    "40": ShopPackName.CRYSTALS_40.value,
    "100": ShopPackName.CRYSTALS_100.value,
}


class PaymentOutcome(str, Enum):
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    SUCCESS = "success"


class InvoiceBuildOutcome(str, Enum):
    OK = "ok"
    NO_YOOKASSA = "no_yookassa"
    INVALID = "invalid"


@dataclass(frozen=True)
class InvoicePriceLine:
    label: str
    amount: int


@dataclass(frozen=True)
class PaymentInvoiceDraft:
    title: str
    description: str
    payload: str
    currency: str
    prices: tuple[InvoicePriceLine, ...]
    provider_token: str = ""
    confirmation_url: str = ""
    save_payment_method: bool = False


@dataclass(frozen=True)
class InvoiceBuildResult:
    outcome: InvoiceBuildOutcome
    draft: PaymentInvoiceDraft | None = None


@dataclass(frozen=True)
class PaymentResult:
    outcome: PaymentOutcome
    energy_credited: int = 0
    crystals_credited: int = 0
    tariff_activated: str = ""
    referral_crystals_to_inviter: int = 0
    save_payment_method: bool = False
    charge_id: str = ""


def normalize_pack_name(pack_name: str) -> str | None:
    raw = (pack_name or "").strip()
    if raw in SHOP_PACKS:
        return raw
    key = raw.lower().replace(" ", "_")
    if key in _PACK_ALIASES:
        return _PACK_ALIASES[key]
    if key.startswith("crystals_"):
        return key
    return None


def pack_name_from_catalog_index(index: int) -> str | None:
    from services.payments_catalog import PACK_CATALOG_ORDER

    if index < 0 or index >= len(PACK_CATALOG_ORDER):
        return None
    return PACK_CATALOG_ORDER[index]


def catalog_index_from_pack_name(pack_name: str) -> int | None:
    for idx in range(len(paycat.PACKAGES)):
        if pack_name_from_catalog_index(idx) == pack_name:
            return idx
    return None


def _pack_paid_energy(spec: dict[str, Any]) -> int:
    return int(spec.get("paid_energy") or spec.get("energy_paid") or 0)


def _pack_expires_at(spec: dict[str, Any]) -> str | None:
    days = spec.get("days")
    if days is None:
        return None
    return (date.today() + timedelta(days=int(days))).isoformat()


async def process_purchase(user_id: int, pack_name: str) -> PurchaseResult:
    """
    Бизнес-цепочка покупки:
    1) ``grant_balance_package`` покупателю;
    2) активация DUO-владельца (только ULTRA 1 месяц);
    3) ``mark_first_purchase_done`` + реферальные +5 💎 инвайтеру.
    """
    normalized = normalize_pack_name(pack_name)
    if not normalized or normalized not in SHOP_PACKS:
        return PurchaseResult(ok=False, pack_name=pack_name, error="unknown_pack")

    spec = SHOP_PACKS[normalized]
    energy_add = _pack_paid_energy(spec)
    crystals_add = int(spec["crystals"])
    tariff_raw = spec.get("tariff")
    duo_access = bool(spec.get("duo_access") or spec.get("family_access"))
    expires_at = _pack_expires_at(spec)

    await store.init_billing_schema()
    await store.grant_balance_package(
        user_id,
        kind=normalized,
        energy_amount=energy_add,
        crystals_amount=crystals_add,
        expires_at=expires_at,
    )

    if duo_access:
        await activate_duo_owner(user_id)
    elif tariff_raw:
        await set_user_tariff(user_id, str(tariff_raw).upper())

    inviter = await store.mark_first_purchase_done(user_id)
    referral_bonus = 0
    if inviter and inviter > 0:
        referral_bonus = REFERRAL_FIRST_PURCHASE_CRYSTALS
        await store.grant_balance_package(
            inviter,
            kind="referral_first_purchase",
            energy_amount=0,
            crystals_amount=referral_bonus,
            expires_at=None,
        )

    tariff_updated = TariffTier.from_db(str(tariff_raw)) if tariff_raw else None
    logger.info(
        "purchase user_id=%s pack=%s energy+%s crystals+%s tariff=%s referral_to=%s",
        user_id,
        normalized,
        energy_add,
        crystals_add,
        tariff_raw,
        inviter,
    )
    return PurchaseResult(
        ok=True,
        pack_name=normalized,
        tariff_updated=tariff_updated,
        energy_paid_added=energy_add,
        crystals_added=crystals_add,
        referral_crystals_to_inviter=referral_bonus if inviter else 0,
    )


def validate_pre_checkout_payload(payload: str, from_user_id: int) -> bool:
    """``pre_checkout_query``: пакет должен существовать в ``SHOP_PACKS``."""
    parsed = paycat.parse_invoice_payload(payload or "")
    if not parsed:
        return False
    uid, pkg_i, _method = parsed
    if uid != from_user_id:
        return False
    pack_name = pack_name_from_catalog_index(pkg_i)
    return pack_name is not None and pack_name in SHOP_PACKS


async def fulfill_payment(
    payer_id: int,
    pack_name: str,
    charge_id: str,
    method: str,
    *,
    save_payment_method: bool = False,
) -> PaymentResult:
    """Идемпотентное начисление после успешной оплаты (Stars / ЮKassa)."""
    normalized = normalize_pack_name(pack_name)
    if not normalized or normalized not in SHOP_PACKS:
        metrics.incr("payment.invalid", {"reason": "unknown_pack"})
        return PaymentResult(outcome=PaymentOutcome.INVALID)

    if not charge_id:
        metrics.incr("payment.invalid", {"reason": "no_charge_id"})
        return PaymentResult(outcome=PaymentOutcome.INVALID)

    spec = SHOP_PACKS[normalized]
    energy = _pack_paid_energy(spec)
    crystals = int(spec["crystals"])
    if not await claim_payment_charge(charge_id, payer_id, energy + crystals):
        metrics.incr("payment.duplicate")
        return PaymentResult(outcome=PaymentOutcome.DUPLICATE, charge_id=charge_id)

    try:
        await ensure_user(payer_id)
        purchase = await process_purchase(payer_id, normalized)
        if not purchase.ok:
            metrics.incr("payment.failed", {"reason": "process_purchase_not_ok"})
            logger.critical(
                "Payment failed for user %s (process_purchase not_ok) charge_id=%s pack=%s",
                payer_id,
                charge_id,
                normalized,
            )
            return PaymentResult(outcome=PaymentOutcome.INVALID, charge_id=charge_id)

        pack_idx = catalog_index_from_pack_name(normalized)
        if pack_idx is None:
            pack_idx = 0
        catalog_pack = paycat.PACKAGES[pack_idx]
        amount = catalog_pack.rub_kopecks if method == "r" else catalog_pack.stars
        currency = "RUB" if method == "r" else "XTR"
        await insert_payment_event(payer_id, catalog_pack.tariff, method, amount, currency)
    except Exception:
        metrics.incr("payment.failed", {"reason": "post_claim_exception"})
        logger.critical(
            "Payment failed for user %s charge_id=%s pack=%s — post-claim crash",
            payer_id,
            charge_id,
            normalized,
            exc_info=True,
        )
        raise

    tariff_label = (
        purchase.tariff_updated.value if purchase.tariff_updated else str(spec.get("tariff") or "")
    )
    metrics.incr("payment.success", {"method": method, "pack": normalized})
    return PaymentResult(
        outcome=PaymentOutcome.SUCCESS,
        energy_credited=purchase.energy_paid_added,
        crystals_credited=purchase.crystals_added,
        tariff_activated=tariff_label,
        referral_crystals_to_inviter=purchase.referral_crystals_to_inviter,
        save_payment_method=save_payment_method,
        charge_id=charge_id,
    )


async def handle_telegram_stars_payment(
    payer_telegram_id: int,
    invoice_payload: str,
    telegram_charge_id: str | None,
    provider_charge_id: str | None,
    *,
    fallback_charge_id: str | None = None,
) -> PaymentResult:
    parsed = paycat.parse_invoice_payload(invoice_payload or "")
    if not parsed:
        metrics.incr("payment.invalid", {"reason": "bad_payload"})
        return PaymentResult(outcome=PaymentOutcome.INVALID)
    uid, pkg_i, method = parsed
    if uid != payer_telegram_id:
        metrics.incr("payment.invalid", {"reason": "uid_mismatch"})
        return PaymentResult(outcome=PaymentOutcome.INVALID)

    pack_name = pack_name_from_catalog_index(pkg_i)
    if not pack_name:
        metrics.incr("payment.invalid", {"reason": "unknown_pack_index"})
        return PaymentResult(outcome=PaymentOutcome.INVALID)

    charge_id = (telegram_charge_id or provider_charge_id or fallback_charge_id or "").strip()
    return await fulfill_payment(uid, pack_name, charge_id, method or "x")


async def handle_yookassa_webhook(body: dict[str, Any]) -> PaymentResult:
    """
    Обработка ``payment.succeeded`` от ЮKassa (прямой API, не Telegram Payments).
    """
    event = str(body.get("event") or "")
    if event != "payment.succeeded":
        return PaymentResult(outcome=PaymentOutcome.IGNORED)

    payment = body.get("object") or {}
    charge_id = str(payment.get("id") or "").strip()
    metadata = payment.get("metadata") or {}
    try:
        user_id = int(metadata.get("user_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    pack_name = str(metadata.get("pack_name") or "")
    saved = bool(payment.get("payment_method", {}).get("saved"))
    save_flag = bool(payment.get("save_payment_method")) or saved

    if not user_id or not pack_name or not charge_id:
        metrics.incr("payment.invalid", {"reason": "yookassa_bad_metadata"})
        return PaymentResult(outcome=PaymentOutcome.INVALID)

    return await fulfill_payment(
        user_id,
        pack_name,
        charge_id,
        "r",
        save_payment_method=save_flag,
    )


def _invoice_draft_from_pack(
    cfg: Settings,
    user_id: int,
    pkg_index: int,
    method: str,
) -> PaymentInvoiceDraft | None:
    if method not in ("r", "x") or pkg_index < 0 or pkg_index >= len(paycat.PACKAGES):
        return None
    pack = paycat.PACKAGES[pkg_index]
    payload = paycat.build_invoice_payload(user_id, pkg_index, method)
    currency = paycat.invoice_currency(method)
    raw_prices = paycat.labeled_prices_for(pack, method)
    prices = tuple(InvoicePriceLine(label=lp.label, amount=lp.amount) for lp in raw_prices)
    title = f"{pack.tariff} · {cfg.shop_payment_title}"[:32]
    description = (
        f"Тариф {pack.tariff}: +{pack.energy} ⚡️ и +{pack.crystals} 💎"
        if pack.energy > 0
        else f"Кристаллы: +{pack.crystals} 💎"
    )
    ptoken = paycat.provider_token_for(method, cfg.payment_token.strip())
    return PaymentInvoiceDraft(
        title=title,
        description=description[:255],
        payload=payload,
        currency=currency,
        prices=prices,
        provider_token=ptoken,
    )


async def create_telegram_stars_invoice(
    cfg: Settings,
    user_id: int,
    pkg_index: int,
) -> InvoiceBuildResult:
    draft = _invoice_draft_from_pack(cfg, user_id, pkg_index, "x")
    if draft is None:
        return InvoiceBuildResult(outcome=InvoiceBuildOutcome.INVALID)
    return InvoiceBuildResult(outcome=InvoiceBuildOutcome.OK, draft=draft)


async def create_yookassa_invoice(
    cfg: Settings,
    user_id: int,
    pkg_index: int,
) -> InvoiceBuildResult:
    """
  ЮKassa: при наличии ``yookassa_shop_id`` + ``yookassa_secret_key`` — прямой
    платёж с ``save_payment_method=True``. Иначе — Telegram Payments (RUB).
    """
    pack_name = pack_name_from_catalog_index(pkg_index)
    if not pack_name or pkg_index < 0 or pkg_index >= len(paycat.PACKAGES):
        return InvoiceBuildResult(outcome=InvoiceBuildOutcome.INVALID)

    shop_id = (cfg.yookassa_shop_id or "").strip()
    secret = (cfg.yookassa_secret_key or "").strip()
    if shop_id and secret:
        pack = paycat.PACKAGES[pkg_index]
        amount_rub = f"{pack.rub_kopecks / 100:.2f}"
        payload = {
            "amount": {"value": amount_rub, "currency": "RUB"},
            "capture": True,
            "save_payment_method": True,
            "confirmation": {
                "type": "redirect",
                "return_url": cfg.yookassa_return_url,
            },
            "description": f"NeuroMule {pack.tariff}"[:128],
            "metadata": {
                "user_id": str(user_id),
                "pack_name": pack_name,
                "pkg_index": str(pkg_index),
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.yookassa.ru/v3/payments",
                auth=(shop_id, secret),
                json=payload,
                headers={"Idempotence-Key": uuid.uuid4().hex},
            )
            resp.raise_for_status()
            data = resp.json()
        confirmation = (data.get("confirmation") or {}).get("confirmation_url") or ""
        draft = PaymentInvoiceDraft(
            title=f"{pack.tariff} · {cfg.shop_payment_title}"[:32],
            description=f"Оплата картой · {pack.energy} ⚡ + {pack.crystals} 💎",
            payload=paycat.build_invoice_payload(user_id, pkg_index, "r"),
            currency="RUB",
            prices=(InvoicePriceLine(label="Итого", amount=pack.rub_kopecks),),
            confirmation_url=confirmation,
            save_payment_method=True,
        )
        return InvoiceBuildResult(outcome=InvoiceBuildOutcome.OK, draft=draft)

    if not cfg.payment_token.strip():
        return InvoiceBuildResult(outcome=InvoiceBuildOutcome.NO_YOOKASSA)

    draft = _invoice_draft_from_pack(cfg, user_id, pkg_index, "r")
    if draft is None:
        return InvoiceBuildResult(outcome=InvoiceBuildOutcome.INVALID)
    return InvoiceBuildResult(outcome=InvoiceBuildOutcome.OK, draft=draft)
