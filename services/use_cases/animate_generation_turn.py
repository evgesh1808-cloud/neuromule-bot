"""Use-case: оживление фото — billing + ``fire_animate_job``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings, settings as app_settings
from content import messages as msg
from services.animate_video_lock import (
    DEFAULT_ANIMATE_LOCK_TTL_SEC,
    is_animate_video_locked,
    release_animate_video_lock,
    try_acquire_animate_video_lock,
)
from services.billing import billing
from services.god_mode import admin_animate_bypass
from services.generation_jobs import GenTask, fire_animate_job, make_animate_task_id
from services.repository import get_user_row
from services.tariffs import can_use_animate, normalize_tariff

if TYPE_CHECKING:
    from aiogram import Bot


class AnimateGenOutcome(str, Enum):
    NEED_PHOTO = "need_photo"
    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"
    FREE_PREMIUM_BLOCKED = "free_premium_blocked"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    ALREADY_GENERATING = "already_generating"
    SUCCESS = "success"


@dataclass(frozen=True)
class AnimateGenResult:
    outcome: AnimateGenOutcome
    upgrade_to: str | None = None


async def run_animate_generation_turn(
    *,
    uid: int,
    telegram_file_id: str,
    bot: "Bot",
    chat_id: int | None = None,
    settings: Settings | None = None,
    motion_prompt: str | None = None,
) -> AnimateGenResult:
    cfg = settings or app_settings
    cid = chat_id if chat_id is not None else uid
    photo_id = (telegram_file_id or "").strip()
    if not photo_id:
        return AnimateGenResult(outcome=AnimateGenOutcome.NEED_PHOTO)

    if await is_animate_video_locked(uid):
        if admin_animate_bypass(uid):
            await release_animate_video_lock(uid)
        else:
            return AnimateGenResult(outcome=AnimateGenOutcome.ALREADY_GENERATING)

    row = await get_user_row(uid)
    admin_bypass = admin_animate_bypass(uid)
    tariff = normalize_tariff(row.tariff)
    if not admin_bypass and not can_use_animate(tariff):
        return AnimateGenResult(outcome=AnimateGenOutcome.FORBIDDEN_BY_TARIFF, upgrade_to="ultra")

    min_cost = int(getattr(cfg, "cost_animate", 20) or 20)
    if not admin_bypass and int(row.crystals or 0) < min_cost:
        return AnimateGenResult(outcome=AnimateGenOutcome.INSUFFICIENT_BALANCE)

    spend = await billing.spend_animate(uid)
    if not spend.ok:
        if spend.error == "free_premium_create_blocked":
            return AnimateGenResult(outcome=AnimateGenOutcome.FREE_PREMIUM_BLOCKED)
        return AnimateGenResult(outcome=AnimateGenOutcome.INSUFFICIENT_BALANCE)

    charge = spend.charge
    assert charge is not None

    if not await try_acquire_animate_video_lock(uid, ttl_sec=DEFAULT_ANIMATE_LOCK_TTL_SEC, settings=cfg):
        if charge.charge_id:
            await billing.refund_charge(charge.charge_id)
        return AnimateGenResult(outcome=AnimateGenOutcome.ALREADY_GENERATING)

    cleaned_prompt = (motion_prompt or "").strip() or None
    new_task = GenTask(
        task_id=make_animate_task_id(uid),
        bot=bot,
        chat_id=cid,
        user_id=uid,
        task_type="animate",
        status="pending",
        file_id=photo_id,
        prompt=cleaned_prompt,
        charged_crystals=charge.crystals,
        billing_charge_id=charge.charge_id,
    )
    try:
        fire_animate_job(new_task)
    except Exception:
        await release_animate_video_lock(uid)
        if charge.charge_id:
            await billing.refund_charge(charge.charge_id)
        raise

    from services.last_animate_request import remember as remember_last_animate

    remember_last_animate(uid, source_file_id=photo_id, motion_prompt=cleaned_prompt)

    await bot.send_message(cid, msg.TXT_ANIMATE_QUEUE_ACCEPTED)
    return AnimateGenResult(outcome=AnimateGenOutcome.SUCCESS)
