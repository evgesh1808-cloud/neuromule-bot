"""
Use-case: приём промпта для генерации изображения после выбора модели.

FREE-слот: только user_daily_quotas (⚡ не списывается).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.billing import billing
from services.billing.image_pipeline import FREE_PHOTO_MODEL_KEY, free_tier_image_model
from services.generation_jobs import fire_photo_job

if TYPE_CHECKING:
    from aiogram import Bot


class PhotoGenOutcome(str, Enum):
    NEED_PROMPT = "need_prompt"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    GLOBAL_FREE_IMAGE_CAP = "global_free_image_cap"
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
    *,
    telegram_file_id: str | None = None,
) -> PhotoGenResult:
    if not prompt and not telegram_file_id:
        return PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)

    model_id = image_model_id or free_tier_image_model()
    spend = await billing.spend_image_resource(user_id, model_id)
    if not spend.ok:
        if spend.error == "global_free_image_cap":
            return PhotoGenResult(outcome=PhotoGenOutcome.GLOBAL_FREE_IMAGE_CAP)
        if spend.error == "daily_limit_exceeded":
            return PhotoGenResult(outcome=PhotoGenOutcome.DAILY_LIMIT_EXCEEDED)
        if spend.error in ("free_image_model_blocked",):
            return PhotoGenResult(outcome=PhotoGenOutcome.FREE_IMAGE_MODEL_BLOCKED)
        return PhotoGenResult(outcome=PhotoGenOutcome.INSUFFICIENT_BALANCE)

    charge = spend.charge
    assert charge is not None
    from services.repository import get_user_row
    from services.tariffs import TariffName, normalize_tariff, queue_priority_for_tariff

    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    priority = queue_priority_for_tariff(tariff)

    effective_model = model_id if model_id else FREE_PHOTO_MODEL_KEY
    fire_photo_job(
        bot,
        chat_id,
        user_id,
        effective_model,
        image_model_label or "Nano Banana (FREE)",
        prompt or "Улучши это фото",
        charge.used_photo_free_slot,
        charge.crystals,
        priority=priority,
        billing_charge_id=charge.charge_id,
        telegram_file_id=telegram_file_id,
    )
    return PhotoGenResult(outcome=PhotoGenOutcome.SUCCESS, vip_priority=(tariff is TariffName.ULTRA))
