"""Use-case: оживление фото — billing + ``fire_animate_job``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings, settings as app_settings
from content import messages as msg
from services.billing import billing
from services.generation_jobs import GenTask, fire_animate_job, make_animate_task_id
from services.repository import get_user_row
from services.tariffs import can_use_animate, normalize_tariff

if TYPE_CHECKING:
    from aiogram import Bot


class AnimateGenOutcome(str, Enum):
    NEED_PHOTO = "need_photo"
    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"
    INSUFFICIENT_BALANCE = "insufficient_balance"
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
) -> AnimateGenResult:
    _ = settings or app_settings
    cid = chat_id if chat_id is not None else uid
    photo_id = (telegram_file_id or "").strip()
    if not photo_id:
        return AnimateGenResult(outcome=AnimateGenOutcome.NEED_PHOTO)

    row = await get_user_row(uid)
    tariff = normalize_tariff(row.tariff)
    if not can_use_animate(tariff):
        return AnimateGenResult(outcome=AnimateGenOutcome.FORBIDDEN_BY_TARIFF, upgrade_to="ultra")

    spend = await billing.spend_animate(uid)
    if not spend.ok:
        return AnimateGenResult(outcome=AnimateGenOutcome.INSUFFICIENT_BALANCE)

    charge = spend.charge
    assert charge is not None
    new_task = GenTask(
        task_id=make_animate_task_id(uid),
        bot=bot,
        chat_id=cid,
        user_id=uid,
        task_type="animate",
        status="pending",
        file_id=photo_id,
        charged_crystals=charge.crystals,
        billing_charge_id=charge.charge_id,
    )
    fire_animate_job(new_task)
    await bot.send_message(cid, msg.TXT_ANIMATE_QUEUE_ACCEPTED)
    return AnimateGenResult(outcome=AnimateGenOutcome.SUCCESS)
