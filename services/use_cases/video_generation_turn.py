"""Use-case: PRO-видео и сценарии пранков — billing + очередь generation_jobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from config import Settings
from content import messages as msg
from services.billing import billing
from services.billing.video_pipeline import (
    resolve_video_prompt,
    scenario_requires_user_photo,
    scenario_requires_user_text,
)
from services.generation_jobs import GenTask, fire_video_job, make_video_task_id
from services.repository import get_user_row
from services.tariffs import TariffName, normalize_tariff, queue_priority_for_tariff

if TYPE_CHECKING:
    from aiogram import Bot


class VideoGenOutcome(str, Enum):
    NEED_PROMPT = "need_prompt"
    NEED_PHOTO = "need_photo"
    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    UNKNOWN_SCENARIO = "unknown_scenario"
    SUCCESS = "success"


@dataclass(frozen=True)
class VideoGenResult:
    outcome: VideoGenOutcome
    upgrade_to: str | None = None
    vip_priority: bool = False
    scenario_id: str = ""


async def run_video_scenario_turn(
    settings: Settings,
    bot: "Bot",
    chat_id: int,
    user_id: int,
    scenario_id: str,
    *,
    user_prompt: str = "",
    telegram_file_id: str = "",
) -> VideoGenResult:
    """Списание по сценарию и постановка задачи в очередь."""
    _ = settings
    sid = (scenario_id or "").strip()
    if not sid:
        return VideoGenResult(outcome=VideoGenOutcome.UNKNOWN_SCENARIO)

    spend = await billing.spend_video_scenario(user_id, sid)
    if not spend.ok:
        if spend.error == "ultra_required":
            row = await get_user_row(user_id)
            tariff = normalize_tariff(row.tariff)
            upgrade = "smart" if tariff in (TariffName.FREE, TariffName.MINI) else "ultra"
            return VideoGenResult(outcome=VideoGenOutcome.FORBIDDEN_BY_TARIFF, upgrade_to=upgrade)
        if spend.error == "insufficient_crystals":
            return VideoGenResult(outcome=VideoGenOutcome.INSUFFICIENT_BALANCE)
        return VideoGenResult(outcome=VideoGenOutcome.UNKNOWN_SCENARIO)

    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    billing_user = await billing.load_user(user_id)
    route = billing.resolve_video_route(sid, billing_user.current_tariff)
    priority = route.queue_priority if route else queue_priority_for_tariff(tariff)

    charge = spend.charge
    assert charge is not None
    prompt = resolve_video_prompt(sid, user_prompt)

    task = GenTask(
        task_id=make_video_task_id(user_id),
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        task_type="video",
        status="pending",
        prompt=prompt,
        file_id=(telegram_file_id or "").strip() or None,
        scenario_id=sid,
        charged_crystals=charge.crystals,
        billing_charge_id=charge.charge_id,
    )
    fire_video_job(task, priority=priority)
    await bot.send_message(chat_id, msg.TXT_VIDEO_QUEUE_ACCEPTED)
    return VideoGenResult(
        outcome=VideoGenOutcome.SUCCESS,
        vip_priority=(tariff is TariffName.ULTRA),
        scenario_id=sid,
    )


async def run_video_generation_turn(
    settings: Settings,
    bot: "Bot",
    chat_id: int,
    user_id: int,
    user_prompt: str,
) -> VideoGenResult:
    """Legacy: кастомный промпт = PRO 5 сек."""
    prompt = (user_prompt or "").strip()
    if not prompt:
        return VideoGenResult(outcome=VideoGenOutcome.NEED_PROMPT)
    return await run_video_scenario_turn(
        settings,
        bot,
        chat_id,
        user_id,
        "video_pro_5sec",
        user_prompt=prompt,
    )


def classify_scenario_pick(scenario_id: str) -> VideoGenOutcome | None:
    """Если сценарий требует фото/текста до списания — вернуть NEED_*."""
    if scenario_requires_user_photo(scenario_id):
        return VideoGenOutcome.NEED_PHOTO
    if scenario_requires_user_text(scenario_id) and scenario_id != "video_pro_5sec":
        return VideoGenOutcome.NEED_PROMPT
    return None
