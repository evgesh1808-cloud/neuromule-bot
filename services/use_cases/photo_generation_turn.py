"""
Use-case: приём текстового промпта для генерации изображения после выбора модели.

Списание через BillingManager (энергия / кристаллы / free-слот Imagen 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.billing import billing
from services.generation_jobs import fire_photo_job

if TYPE_CHECKING:
    from aiogram import Bot


class PhotoGenOutcome(str, Enum):
    NEED_PROMPT = "need_prompt"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    FREE_IMAGE_MODEL_BLOCKED = "free_image_model_blocked"
    SUCCESS = "success"


@dataclass(frozen=True)
class PhotoGenResult:
    """Результат ``run_photo_generation_turn``."""

    outcome: PhotoGenOutcome
    vip_priority: bool = False


async def run_photo_generation_turn(
    settings: Settings,
    bot: "Bot",
    chat_id: int,
    user_id: int,
    image_model_id: str,
    image_model_label: str,
    prompt: str,
) -> PhotoGenResult:
    if not prompt:
        return PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)

    spend = await billing.spend_image_resource(user_id, image_model_id)
    if not spend.ok:
        if spend.error == "daily_limit_exceeded":
            return PhotoGenResult(outcome=PhotoGenOutcome.DAILY_LIMIT_EXCEEDED)
        if spend.error in ("free_image_model_blocked",):
            return PhotoGenResult(outcome=PhotoGenOutcome.FREE_IMAGE_MODEL_BLOCKED)
        return PhotoGenResult(outcome=PhotoGenOutcome.INSUFFICIENT_BALANCE)

    charge = spend.charge
    assert charge is not None
    from services.tariffs import TariffName, normalize_tariff, queue_priority_for_tariff
    from services.repository import get_user_row

    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    priority = queue_priority_for_tariff(tariff)

    fire_photo_job(
        bot,
        chat_id,
        user_id,
        image_model_id,
        image_model_label,
        prompt,
        charge.used_photo_free_slot,
        charge.crystals,
        priority=priority,
        billing_charge_id=charge.charge_id,
    )
    return PhotoGenResult(outcome=PhotoGenOutcome.SUCCESS, vip_priority=(tariff is TariffName.ULTRA))
