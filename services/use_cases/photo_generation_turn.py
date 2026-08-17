"""
Use-case: приём промпта для генерации изображения после выбора модели.

FREE-слот: только user_daily_quotas (⚡ не списывается).
Постановка в очередь — на call-site после отправки status_msg (см. generation_fsm).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from services.billing import billing
from services.billing.image_pipeline import FREE_PHOTO_MODEL_KEY, free_tier_image_model
from services.photo_aspect_ratio import DEFAULT_PHOTO_ASPECT_RATIO, normalize_photo_aspect_ratio

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
class PhotoEnqueueSpec:
    """Данные для ``fire_photo_job`` после отправки status_msg."""

    image_model_id: str
    model_label: str
    prompt: str
    used_daily_slot: bool
    charged_crystals: int
    priority: int
    billing_charge_id: str
    telegram_file_id: str | None = None
    reference_image_url: str | None = None
    reference_image_bytes: bytes | None = None
    reference_mime: str = "image/jpeg"
    aspect_ratio: str = "1:1"
    composite_refine: bool = False
    composite_base_file_id: str | None = None
    composite_base_reference_url: str | None = None
    composite_base_reference_bytes: bytes | None = None


@dataclass(frozen=True)
class PhotoGenResult:
    """Результат ``run_photo_generation_turn``."""

    outcome: PhotoGenOutcome
    vip_priority: bool = False
    enqueue: PhotoEnqueueSpec | None = None


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
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
    aspect_ratio: str | None = None,
    composite_refine: bool = False,
    composite_base_file_id: str | None = None,
    composite_base_reference_url: str | None = None,
    composite_base_reference_bytes: bytes | None = None,
) -> PhotoGenResult:
    # bot/chat_id/settings — контракт call-site; списание через billing store.
    _ = (settings, bot, chat_id)
    tg_ref = (telegram_file_id or "").strip() or None
    url_ref = (reference_image_url or "").strip() or None
    bytes_ref: bytes | None = None
    if reference_image_bytes is not None:
        if isinstance(reference_image_bytes, memoryview):
            bytes_ref = reference_image_bytes.tobytes()
        elif isinstance(reference_image_bytes, (bytes, bytearray)):
            bytes_ref = bytes(reference_image_bytes)
        else:
            raise TypeError("reference_image_bytes must be bytes")

    base_file_id = (composite_base_file_id or "").strip() or None
    base_url = (composite_base_reference_url or "").strip() or None
    base_bytes: bytes | None = None
    if composite_base_reference_bytes is not None:
        if isinstance(composite_base_reference_bytes, memoryview):
            base_bytes = composite_base_reference_bytes.tobytes()
        elif isinstance(composite_base_reference_bytes, (bytes, bytearray)):
            base_bytes = bytes(composite_base_reference_bytes)
        else:
            raise TypeError("composite_base_reference_bytes must be bytes")

    object_sources = sum(x is not None for x in (tg_ref, url_ref, bytes_ref))
    base_sources = sum(x is not None for x in (base_file_id, base_url, base_bytes))

    if composite_refine:
        if object_sources != 1 or base_sources != 1:
            raise ValueError(
                "photo_generation_turn: composite refine requires exactly one object "
                "and one base reference source"
            )
    elif object_sources > 1:
        raise ValueError("photo_generation_turn: only one reference source allowed")

    if not prompt and object_sources == 0 and not composite_refine:
        return PhotoGenResult(outcome=PhotoGenOutcome.NEED_PROMPT)
    if composite_refine and not prompt:
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
    return PhotoGenResult(
        outcome=PhotoGenOutcome.SUCCESS,
        vip_priority=(tariff is TariffName.ULTRA),
        enqueue=PhotoEnqueueSpec(
            image_model_id=effective_model,
            model_label=image_model_label or "Flux FREE",
            prompt=prompt or "Улучши это фото",
            used_daily_slot=charge.used_photo_free_slot,
            charged_crystals=charge.crystals,
            priority=priority,
            billing_charge_id=charge.charge_id,
            telegram_file_id=tg_ref,
            reference_image_url=url_ref,
            reference_image_bytes=bytes_ref,
            reference_mime=reference_mime or "image/jpeg",
            aspect_ratio=normalize_photo_aspect_ratio(aspect_ratio),
            composite_refine=composite_refine,
            composite_base_file_id=base_file_id,
            composite_base_reference_url=base_url,
            composite_base_reference_bytes=base_bytes,
        ),
    )
