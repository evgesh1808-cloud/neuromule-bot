"""Use-case Музыкальной студии: тариф/баланс → ``fire_music_job`` → Suno AI.

Поддерживает три режима NeuroMule 🐎⚡️:

* ``ai_lyrics`` — ИИ-сценарист пишет и текст, и стиль.
* ``custom_lyrics`` — пользователь даёт lyrics, ИИ-режиссёр шьёт стиль.
* ``instrumental`` — только минус, ``make_instrumental=True``.

Биллинг строго ``crystals_only=True`` (15 💎, no energy). FREE — жёсткий
блок ещё до списания. Платный юзер без 15 💎 получает ``INSUFFICIENT_BALANCE``
и UI с кнопками покупки 40/100 💎.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

from aiogram.enums import ParseMode

from config import Settings, settings as app_settings
from content import messages as msg
from services.billing import billing
from services.generation_jobs import GenTask, fire_music_job, make_music_task_id
from services.repository import get_user_row
from services.tariffs import can_use_music, normalize_tariff

if TYPE_CHECKING:
    from aiogram import Bot


MusicMode = Literal["ai_lyrics", "custom_lyrics", "instrumental"]


class MusicGenOutcome(str, Enum):
    NEED_HINT = "need_hint"
    FORBIDDEN_BY_TARIFF = "forbidden_by_tariff"
    FREE_PREMIUM_BLOCKED = "free_premium_blocked"
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
    mode: MusicMode = "ai_lyrics",
    lyrics: str | None = None,
    continue_clip_id: str | None = None,
) -> MusicGenResult:
    """Списание 15 💎 + постановка задачи в Suno-очередь NeuroMule."""

    cfg = settings or app_settings
    cid = chat_id if chat_id is not None else uid
    prompt = (style_prompt or "").strip()
    if not prompt:
        return MusicGenResult(outcome=MusicGenOutcome.NEED_HINT)

    row = await get_user_row(uid)
    tariff = normalize_tariff(row.tariff)
    if not can_use_music(tariff):
        return MusicGenResult(
            outcome=MusicGenOutcome.FREE_PREMIUM_BLOCKED,
            upgrade_to="mini",
        )

    _ = cfg
    spend = await billing.spend_music(uid)
    if not spend.ok:
        if spend.error == "free_premium_create_blocked":
            return MusicGenResult(outcome=MusicGenOutcome.FREE_PREMIUM_BLOCKED)
        if spend.error == "music_smart_tariff_only":
            return MusicGenResult(outcome=MusicGenOutcome.FORBIDDEN_BY_TARIFF, upgrade_to="mini")
        return MusicGenResult(outcome=MusicGenOutcome.INSUFFICIENT_BALANCE)

    charge = spend.charge
    assert charge is not None

    cleaned_lyrics = (lyrics or "").strip() if mode == "custom_lyrics" else None
    is_instrumental = mode == "instrumental"

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
        music_lyrics=cleaned_lyrics,
        music_instrumental=is_instrumental,
        music_continue_clip_id=continue_clip_id,
    )

    fire_music_job(new_task)

    await bot.send_message(cid, msg.TXT_MUSIC_QUEUE_ACCEPTED, parse_mode=ParseMode.HTML)

    return MusicGenResult(outcome=MusicGenOutcome.SUCCESS)
