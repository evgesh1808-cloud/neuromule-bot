"""Асинхронные «задачи генерации»: статус сразу, результат позже (заглушка под реальный API)."""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from aiogram.types import BufferedInputFile

from config import settings
from content import messages as msg
from content.inline_keyboards import result_music_keyboard, result_photo_keyboard, result_video_keyboard
from services.repository import get_user_row, rollback_daily_photo_slot, update_balance

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

# Минимальный PNG для заглушки результата (реальный API подставит своё медиа).
_MINI_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

JobKind = Literal["photo", "video", "music", "animate"]


@dataclass(frozen=True)
class _GenTask:
    kind: JobKind
    bot: "Bot"
    chat_id: int
    user_id: int
    model_label: str = ""
    user_prompt: str = ""
    used_daily_slot: bool = False
    style_hint: str = ""


_QUEUE: asyncio.PriorityQueue[tuple[int, int, _GenTask]] = asyncio.PriorityQueue()
_SEQ = 0
_WORKER_STARTED = False


def _balance_footer(energy: int) -> str:
    if energy < settings.energy_low_threshold:
        return msg.TXT_BALANCE_LOW_FOOTER
    return ""


async def _photo_stub_worker(
    bot: "Bot",
    chat_id: int,
    user_id: int,
    model_label: str,
    user_prompt: str,
    used_daily_slot: bool,
) -> None:
    try:
        await asyncio.sleep(2.0)
        row = await get_user_row(user_id)
        cap = msg.TXT_RESULT_PHOTO_CAPTION.format(
            cost=settings.cost_image_pro,
            balance=row.energy,
            animate_cost=settings.cost_animate_video_suggest,
        )
        cap += _balance_footer(row.energy)
        photo = BufferedInputFile(_MINI_PNG, filename="neuromule_preview.png")
        await bot.send_photo(
            chat_id,
            photo=photo,
            caption=cap,
            reply_markup=result_photo_keyboard(),
        )
    except Exception:
        logger.exception("photo job failed")
        await update_balance(user_id, "energy", settings.cost_image_pro)
        if used_daily_slot:
            await rollback_daily_photo_slot(user_id)
        try:
            await bot.send_message(chat_id, msg.TXT_GEN_JOB_FAILED)
        except Exception:
            pass


async def _queue_worker() -> None:
    while True:
        _priority, _seq, task = await _QUEUE.get()
        try:
            if task.kind == "photo":
                await _photo_stub_worker(
                    task.bot,
                    task.chat_id,
                    task.user_id,
                    task.model_label,
                    task.user_prompt,
                    task.used_daily_slot,
                )
            elif task.kind == "video":
                await _video_stub_worker(task.bot, task.chat_id, task.user_id)
            elif task.kind == "music":
                await _music_stub_worker(task.bot, task.chat_id, task.user_id, task.style_hint)
            elif task.kind == "animate":
                await _animate_stub_worker(task.bot, task.chat_id, task.user_id)
        finally:
            _QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    _WORKER_STARTED = True
    asyncio.create_task(_queue_worker())


def _enqueue(priority: int, task: _GenTask) -> None:
    global _SEQ
    _ensure_worker()
    _SEQ += 1
    _QUEUE.put_nowait((priority, _SEQ, task))


async def _video_stub_worker(bot: "Bot", chat_id: int, user_id: int) -> None:
    try:
        await asyncio.sleep(2.0)
        row = await get_user_row(user_id)
        cap = msg.TXT_RESULT_VIDEO_CAPTION.format(cost=settings.cost_video)
        cap += _balance_footer(row.energy)
        await bot.send_message(chat_id, cap, reply_markup=result_video_keyboard())
    except Exception:
        logger.exception("video job failed")
        await update_balance(user_id, "energy", settings.cost_video)
        try:
            await bot.send_message(chat_id, msg.TXT_GEN_JOB_FAILED)
        except Exception:
            pass


async def _music_stub_worker(bot: "Bot", chat_id: int, user_id: int, style_hint: str) -> None:
    try:
        await asyncio.sleep(2.0)
        row = await get_user_row(user_id)
        style = style_hint[:120] if style_hint else "по запросу"
        cap = msg.TXT_RESULT_MUSIC_CAPTION.format(
            style=style,
            balance=row.energy,
        )
        cap += _balance_footer(row.energy)
        await bot.send_message(chat_id, cap, reply_markup=result_music_keyboard())
    except Exception:
        logger.exception("music job failed")
        await update_balance(user_id, "energy", settings.cost_music)
        try:
            await bot.send_message(chat_id, msg.TXT_GEN_JOB_FAILED)
        except Exception:
            pass


async def _animate_stub_worker(bot: "Bot", chat_id: int, user_id: int) -> None:
    try:
        await asyncio.sleep(2.0)
        row = await get_user_row(user_id)
        cap = msg.TXT_RESULT_ANIMATE_CAPTION.format(
            cost=settings.cost_animate,
            balance=row.energy,
        )
        cap += _balance_footer(row.energy)
        await bot.send_message(chat_id, cap)
    except Exception:
        logger.exception("animate job failed")
        await update_balance(user_id, "energy", settings.cost_animate)
        try:
            await bot.send_message(chat_id, msg.TXT_GEN_JOB_FAILED)
        except Exception:
            pass


def fire_photo_job(
    bot: "Bot",
    chat_id: int,
    user_id: int,
    model_label: str,
    user_prompt: str,
    used_daily_slot: bool,
    priority: int = 2,
) -> None:
    _enqueue(
        priority,
        _GenTask(
            kind="photo",
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            model_label=model_label,
            user_prompt=user_prompt,
            used_daily_slot=used_daily_slot,
        ),
    )


def fire_video_job(bot: "Bot", chat_id: int, user_id: int, priority: int = 2) -> None:
    _enqueue(
        priority,
        _GenTask(
            kind="video",
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
        ),
    )


def fire_music_job(bot: "Bot", chat_id: int, user_id: int, style_hint: str) -> None:
    asyncio.create_task(_music_stub_worker(bot, chat_id, user_id, style_hint))


def fire_animate_job(bot: "Bot", chat_id: int, user_id: int) -> None:
    asyncio.create_task(_animate_stub_worker(bot, chat_id, user_id))
