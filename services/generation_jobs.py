"""Асинхронные задачи генерации медиа (фото, видео, музыка, оживление).

Поток данных: use-case → fire_*_job → очередь → воркеры.
Ключи: ``REPLICATE_API_TOKEN``, ``SUNO_API_URL`` (см. ``config.Settings``).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from aiogram.types import BufferedInputFile

from config import settings
from content import messages as msg
from content.inline_keyboards import result_music_keyboard, result_photo_keyboard, result_video_keyboard
from platforms.telegram_chat_action import chat_action_loop
from services.replicate_client import (
    call_replicate_model,
    replicate_configured,
    telegram_photo_download_url,
)
from services.suno_client import generate_music_track, suno_configured
from services.repository import get_user_row, rollback_daily_photo_slot, update_balance

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

_MINI_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

JobKind = Literal["photo", "video", "music", "animate"]
TaskStatus = Literal["pending", "processing", "completed", "failed"]


@dataclass
class GenTask:
    """Задание в очереди генерации медиа."""

    task_id: str
    bot: "Bot"
    chat_id: int
    user_id: int
    task_type: JobKind
    status: str = "pending"
    prompt: str | None = None
    file_id: str | None = None
    model_label: str = ""
    used_daily_slot: bool = False
    charged_crystals: int = 0

    @property
    def kind(self) -> JobKind:
        return self.task_type


_GenTask = GenTask  # внутренний алиас

_QUEUE: asyncio.PriorityQueue[tuple[int, int, GenTask]] = asyncio.PriorityQueue()
_SEQ = 0
_WORKER_STARTED = False


def _new_task_id() -> str:
    return uuid.uuid4().hex[:12]


def make_video_task_id(user_id: int) -> str:
    """Уникальный id видео-задачи: vid_{uid}_{loop_time}."""
    return f"vid_{user_id}_{int(asyncio.get_running_loop().time())}"


def make_animate_task_id(user_id: int) -> str:
    """Уникальный id задачи оживления: anim_{uid}_{loop_time}."""
    return f"anim_{user_id}_{int(asyncio.get_running_loop().time())}"


def make_music_task_id(user_id: int) -> str:
    """Уникальный id музыкальной задачи: mus_{uid}_{loop_time}."""
    return f"mus_{user_id}_{int(asyncio.get_running_loop().time())}"


def _balance_footer(crystals: int) -> str:
    if crystals < max(settings.cost_image_pro, settings.cost_music):
        return msg.TXT_BALANCE_LOW_FOOTER
    return ""


async def _photo_stub_worker(task: GenTask) -> None:
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    user_prompt = (task.prompt or "").strip()
    try:
        logger.info(
            "photo job %s user_id=%s model=%s prompt_len=%s",
            task.task_id,
            user_id,
            task.model_label,
            len(user_prompt),
        )
        async with chat_action_loop(bot, chat_id, "upload_photo"):
            await asyncio.sleep(2.0)
            row = await get_user_row(user_id)
            cap = msg.TXT_RESULT_PHOTO_CAPTION.format(
                cost=task.charged_crystals,
                balance=row.crystals,
                animate_cost=settings.cost_animate_video_suggest,
            )
            cap += _balance_footer(row.crystals)
            photo = BufferedInputFile(_MINI_PNG, filename="neuromule_preview.png")
            await bot.send_document(
                chat_id,
                document=photo,
                caption=cap,
                reply_markup=result_photo_keyboard(),
            )
        task.status = "completed"
    except Exception:
        task.status = "failed"
        logger.exception("photo job failed task_id=%s", task.task_id)
        if task.charged_crystals:
            await update_balance(user_id, "crystals", task.charged_crystals)
        if task.used_daily_slot:
            await rollback_daily_photo_slot(user_id)
        try:
            await bot.send_message(chat_id, msg.TXT_GEN_JOB_FAILED)
        except Exception:
            pass


async def _video_stub_worker(task: GenTask) -> None:
    """Видео по тексту: Replicate (модель из REPLICATE_VIDEO_MODEL) или заглушка без токена."""
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    prompt = (task.prompt or "").strip()
    if not prompt:
        prompt = "Кинематографичная сцена, мягкий свет"

    charged = task.charged_crystals or settings.cost_video

    async def _fail() -> None:
        task.status = "failed"
        if charged:
            await update_balance(user_id, "crystals", charged)
        try:
            await bot.send_message(chat_id, msg.TXT_VIDEO_REPLICATE_FAILED)
        except Exception:
            pass

    try:
        logger.info("video job %s user_id=%s replicate=%s", task.task_id, user_id, replicate_configured())
        async with chat_action_loop(bot, chat_id, "upload_video"):
            row = await get_user_row(user_id)
            video_url: str | None = None
            if replicate_configured():
                inputs = {
                    "prompt": prompt,
                    "aspect_ratio": "16:9",
                }
                video_url = await call_replicate_model(settings.replicate_video_model, inputs)

            if video_url:
                caption = f"🎬 Ваше видео по запросу:\n«{prompt[:500]}» успешно готово!"
                caption += _balance_footer(row.crystals)
                await bot.send_video(
                    chat_id,
                    video=video_url,
                    caption=caption,
                    reply_markup=result_video_keyboard(),
                )
            elif not replicate_configured():
                await asyncio.sleep(4.0)
                cap = (
                    f"🎬 Ваше видео по запросу:\n«{prompt[:500]}» "
                    "(демо: задайте REPLICATE_API_TOKEN для реальной генерации)"
                )
                cap += _balance_footer(row.crystals)
                await bot.send_message(chat_id, cap, reply_markup=result_video_keyboard())
            else:
                raise RuntimeError("Replicate returned empty video URL")

        task.status = "completed"
    except Exception as exc:
        logger.error("video job %s failed: %s", task.task_id, exc)
        await _fail()


async def _music_stub_worker(task: GenTask) -> None:
    """Музыка по описанию стиля: Suno API (прокси) или демо без токена."""
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    style = (task.prompt or "").strip()[:500] or "по запросу"

    charged = task.charged_crystals or settings.cost_music

    async def _fail_music() -> None:
        task.status = "failed"
        if charged:
            await update_balance(user_id, "crystals", charged)
        try:
            await bot.send_message(chat_id, msg.TXT_MUSIC_SUNO_FAILED)
        except Exception:
            pass

    try:
        logger.info(
            "music job %s prompt=%r suno=%s",
            task.task_id,
            style[:120],
            suno_configured(),
        )
        async with chat_action_loop(bot, chat_id, "upload_audio"):
            row = await get_user_row(user_id)
            track: tuple[str, str] | None = None
            if suno_configured():
                track = await generate_music_track(style)

            if track:
                audio_url, title = track
                caption = f"🎵 Ваш уникальный трек по запросу:\n«{style[:400]}» успешно записан!"
                caption += "\n\n" + msg.TXT_RESULT_MUSIC_CAPTION.format(
                    style=style[:120],
                    balance=row.crystals,
                )
                caption += _balance_footer(row.crystals)
                await bot.send_audio(
                    chat_id,
                    audio=audio_url,
                    title=title,
                    performer="NeuroMul",
                    caption=caption,
                    reply_markup=result_music_keyboard(),
                )
            elif not suno_configured():
                await asyncio.sleep(2.0)
                cap = msg.TXT_RESULT_MUSIC_CAPTION.format(style=style[:120], balance=row.crystals)
                cap += "\n\n(демо: задайте SUNO_API_TOKEN и URL прокси)"
                cap += _balance_footer(row.crystals)
                await bot.send_message(chat_id, cap, reply_markup=result_music_keyboard())
            else:
                raise RuntimeError("Suno returned empty audio URL")

        task.status = "completed"
    except Exception as exc:
        logger.error("music job %s failed: %s", task.task_id, exc)
        await _fail_music()


async def _animate_stub_worker(task: GenTask) -> None:
    """
    Воркер очереди для оживления фото.
    Использует Telegram file_id исходного снимка из task.file_id.
    """
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    file_id = (task.file_id or "").strip()
    charged = task.charged_crystals or settings.cost_animate
    if not file_id:
        task.status = "failed"
        logger.error("animate job %s: missing file_id user_id=%s", task.task_id, user_id)
        if charged:
            await update_balance(user_id, "crystals", charged)
        try:
            await bot.send_message(chat_id, msg.TXT_ANIMATE_FAILED)
        except Exception:
            pass
        return

    async def _fail_animate() -> None:
        task.status = "failed"
        if charged:
            await update_balance(user_id, "crystals", charged)
        try:
            await bot.send_message(chat_id, msg.TXT_ANIMATE_REPLICATE_FAILED)
        except Exception:
            pass

    try:
        logger.info(
            "animate job %s file_id=%s user_id=%s replicate=%s",
            task.task_id,
            file_id,
            user_id,
            replicate_configured(),
        )
        async with chat_action_loop(bot, chat_id, "upload_video"):
            row = await get_user_row(user_id)
            animated_url: str | None = None

            if replicate_configured():
                image_url = await telegram_photo_download_url(bot, file_id)
                inputs = {
                    "prompt": "Мягкое кинематографичное движение, оживление кадра, реализм",
                    "start_image_url": image_url,
                    "aspect_ratio": "16:9",
                }
                animated_url = await call_replicate_model(settings.replicate_animate_model, inputs)

            if animated_url:
                cap = msg.TXT_ANIMATE_SUCCESS
                cap += "\n\n" + msg.TXT_RESULT_ANIMATE_CAPTION.format(
                    cost=settings.cost_animate,
                    balance=row.crystals,
                )
                cap += _balance_footer(row.crystals)
                await bot.send_video(chat_id, video=animated_url, caption=cap)
            elif not replicate_configured():
                await asyncio.sleep(4.0)
                await bot.send_message(chat_id, msg.TXT_ANIMATE_SUCCESS)
                cap = msg.TXT_ANIMATE_SOURCE_CAPTION + " (демо: REPLICATE_API_TOKEN)"
                cap += "\n\n" + msg.TXT_RESULT_ANIMATE_CAPTION.format(
                    cost=settings.cost_animate,
                    balance=row.crystals,
                )
                cap += _balance_footer(row.crystals)
                await bot.send_photo(chat_id, photo=file_id, caption=cap)
            else:
                raise RuntimeError("Replicate returned empty animate URL")

        task.status = "completed"
    except Exception as exc:
        logger.error("animate job %s failed: %s", task.task_id, exc)
        await _fail_animate()


async def _queue_worker() -> None:
    while True:
        _priority, _seq, task = await _QUEUE.get()
        try:
            if task.task_type == "photo":
                await _photo_stub_worker(task)
            elif task.task_type == "video":
                await _video_stub_worker(task)
            elif task.task_type == "music":
                await _music_stub_worker(task)
            elif task.task_type == "animate":
                await _animate_stub_worker(task)
        finally:
            _QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    _WORKER_STARTED = True
    asyncio.create_task(_queue_worker())


def _enqueue(priority: int, task: GenTask) -> None:
    global _SEQ
    _ensure_worker()
    _SEQ += 1
    _QUEUE.put_nowait((priority, _SEQ, task))


def fire_photo_job(
    bot: "Bot",
    chat_id: int,
    user_id: int,
    model_label: str,
    user_prompt: str,
    used_daily_slot: bool,
    charged_crystals: int,
    priority: int = 2,
) -> None:
    _enqueue(
        priority,
        GenTask(
            task_id=_new_task_id(),
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            task_type="photo",
            prompt=user_prompt,
            model_label=model_label,
            used_daily_slot=used_daily_slot,
            charged_crystals=charged_crystals,
        ),
    )


def fire_video_job(task: GenTask, priority: int = 2) -> None:
    """Ставит готовый ``GenTask`` (video) в фоновую очередь."""
    _enqueue(priority, task)


def fire_music_job(task: GenTask, priority: int = 2) -> None:
    """Ставит готовый ``GenTask`` (music) в фоновую очередь."""
    _enqueue(priority, task)


def fire_animate_job(task: GenTask, priority: int = 2) -> None:
    """Ставит готовый ``GenTask`` (animate) в фоновую очередь."""
    _enqueue(priority, task)
