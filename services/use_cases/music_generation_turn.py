"""Use-case: описание трека — списание кристаллов и постановка ``fire_music_job``."""



from __future__ import annotations



from dataclasses import dataclass

from enum import Enum

from typing import TYPE_CHECKING



from config import Settings, settings as app_settings

from content import messages as msg

from services.generation_jobs import GenTask, fire_music_job, make_music_task_id

from services.billing import billing
from services.repository import get_user_row

from services.tariffs import can_use_music, normalize_tariff



if TYPE_CHECKING:

    from aiogram import Bot





class MusicGenOutcome(str, Enum):

    NEED_HINT = "need_hint"

    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"

    INSUFFICIENT_BALANCE = "insufficient_balance"

    SUCCESS = "success"





@dataclass(frozen=True)

class MusicGenResult:

    outcome: MusicGenOutcome

    upgrade_to: str | None = None





async def run_music_generation_turn(

    *,

    uid: int,

    style_prompt: str,

    bot: "Bot",

    chat_id: int | None = None,

    settings: Settings | None = None,

) -> MusicGenResult:

    """Тариф/баланс → ``GenTask`` с промптом стиля → очередь Suno."""

    cfg = settings or app_settings

    cid = chat_id if chat_id is not None else uid

    prompt = (style_prompt or "").strip()

    if not prompt:

        return MusicGenResult(outcome=MusicGenOutcome.NEED_HINT)



    row = await get_user_row(uid)

    tariff = normalize_tariff(row.tariff)

    if not can_use_music(tariff):

        return MusicGenResult(outcome=MusicGenOutcome.FORBIDDEN_BY_TARIFF, upgrade_to="smart")

    _ = cfg
    spend = await billing.spend_music(uid)
    if not spend.ok:
        return MusicGenResult(outcome=MusicGenOutcome.INSUFFICIENT_BALANCE)

    charge = spend.charge
    assert charge is not None
    new_task = GenTask(
        task_id=make_music_task_id(uid),
        bot=bot,
        chat_id=cid,
        user_id=uid,
        task_type="music",
        status="pending",
        prompt=prompt[:500],
        charged_crystals=charge.crystals,
        billing_charge_id=charge.charge_id,
    )

    fire_music_job(new_task)

    await bot.send_message(cid, msg.TXT_MUSIC_QUEUE_ACCEPTED)



    return MusicGenResult(outcome=MusicGenOutcome.SUCCESS)

