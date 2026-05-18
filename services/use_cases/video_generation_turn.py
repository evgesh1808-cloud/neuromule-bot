"""Use-case: текстовый промпт для видео — списание кристаллов и постановка ``fire_video_job``."""



from __future__ import annotations



from dataclasses import dataclass

from enum import Enum

from typing import TYPE_CHECKING



from config import Settings

from content import messages as msg

from services.generation_jobs import GenTask, fire_video_job, make_video_task_id

from services.repository import get_user_row, try_consume_crystals

from services.tariffs import TariffName, can_use_video, normalize_tariff, queue_priority_for_tariff



if TYPE_CHECKING:

    from aiogram import Bot





class VideoGenOutcome(str, Enum):

    NEED_PROMPT = "need_prompt"

    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"

    INSUFFICIENT_BALANCE = "insufficient_balance"

    SUCCESS = "success"





@dataclass(frozen=True)

class VideoGenResult:

    outcome: VideoGenOutcome

    upgrade_to: str | None = None

    vip_priority: bool = False





async def run_video_generation_turn(

    settings: Settings,

    bot: "Bot",

    chat_id: int,

    user_id: int,

    user_prompt: str,

) -> VideoGenResult:

    """

    Проверка тарифа/баланса → ``GenTask`` с промптом → ``fire_video_job`` → уведомление в чат.



    Вход: settings, bot, chat_id, user_id, user_prompt (текст сцены, уже strip снаружи).

    """

    prompt = (user_prompt or "").strip()

    if not prompt:

        return VideoGenResult(outcome=VideoGenOutcome.NEED_PROMPT)



    row = await get_user_row(user_id)

    tariff = normalize_tariff(row.tariff)

    if not can_use_video(tariff):

        return VideoGenResult(

            outcome=VideoGenOutcome.FORBIDDEN_BY_TARIFF,

            upgrade_to="smart" if tariff in (TariffName.FREE, TariffName.MINI) else "ultra",

        )

    cost = settings.cost_video
    if not await try_consume_crystals(user_id, cost):
        return VideoGenResult(outcome=VideoGenOutcome.INSUFFICIENT_BALANCE)

    new_task = GenTask(
        task_id=make_video_task_id(user_id),
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        task_type="video",
        status="pending",
        prompt=prompt,
        charged_crystals=cost,
    )

    fire_video_job(new_task, priority=queue_priority_for_tariff(tariff))

    await bot.send_message(chat_id, msg.TXT_VIDEO_QUEUE_ACCEPTED)



    return VideoGenResult(outcome=VideoGenOutcome.SUCCESS, vip_priority=(tariff is TariffName.ULTRA))

