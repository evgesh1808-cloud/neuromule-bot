"""Асинхронные задачи генерации медиа (фото, видео, музыка, оживление).

Поток данных: use-case → fire_*_job → очередь → воркеры.
Ключи: ``REPLICATE_API_TOKEN``, ``SUNO_API_URL`` (см. ``config.Settings``).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, InputFile, URLInputFile

from config import settings
from content import messages as msg
from content.inline_keyboards import (
    result_music_keyboard,
    result_music_keyboard_pro,
    result_photo_keyboard,
)
from content.video_menu import result_video_keyboard_pro
from platforms.telegram_chat_action import chat_action_loop
from services import last_music_request, last_share_media
from services.gemini_image_client import (
    GeminiImageResult,
    generate_gemini_image_model,
    generate_imagen_fast,
)
from services.replicate_client import (
    call_replicate_model,
    replicate_configured,
    telegram_photo_download_url,
)
from services.suno_client import SunoTrack, generate_music_track, suno_configured
from business_catalog import catalog
from config import settings as app_settings
from services.api_resilience import ExternalApiError, fail_generation_task, wrap_http_error
from services.billing.translator import (
    enhance_music_style_prompt,
    enhance_video_prompt_for_replicate,
    translate_prompt_to_english,
)
from services.billing.video_pipeline import VIDEO_SCENARIOS
from services.repository import get_user_row

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

JobKind = Literal["photo", "video", "music", "animate"]
TaskStatus = Literal["pending", "processing", "completed", "failed"]

# Жёсткий потолок ожидания ответа от внешних API (Replicate / Suno / Gemini).
# 180 секунд — это верхняя граница для длинного Replicate-видео (5-10 сек
# конечного клипа рендерится 1-3 минуты). Дальше — таймаут и автоматический
# refund списанных Кристаллов через ``fail_generation_task``.
EXTERNAL_API_TIMEOUT_SEC: int = 180


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
    image_model_id: str = ""
    model_label: str = ""
    scenario_id: str = ""
    used_daily_slot: bool = False
    charged_crystals: int = 0
    billing_charge_id: str = ""
    music_lyrics: str | None = None
    music_instrumental: bool = False
    music_continue_clip_id: str | None = None

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


def _remember_share(
    task: GenTask,
    *,
    file_id: str | None = None,
    media_url: str | None = None,
) -> None:
    """Кэш медиа-таска для кнопки ``📢 Поделиться в Галерее``.

    Хранит ``file_id`` (нужен для отправки в TG-канал галереи без повторной
    закачки с внешнего API) и ``media_url`` (нужен для VK / MAX App, которые
    не понимают Telegram file_id). Принимает хотя бы один из аргументов.

    Любые ошибки кэширования проглатываются: side-effect не должен уронить
    основной воркер и сорвать ``fail_generation_task``/refund.
    """

    if not file_id and not media_url:
        return
    try:
        last_share_media.remember(
            user_id=task.user_id,
            task_id=task.task_id,
            task_type=task.task_type,  # type: ignore[arg-type]
            prompt=(task.prompt or "").strip(),
            file_id=file_id,
            media_url=media_url,
        )
    except Exception:
        logger.info("share cache: remember failed task=%s", task.task_id, exc_info=True)


def _normalize_photo_model_id(model_id: str, model_label: str = "") -> str:
    """ID модели из меню (imagen4, flux-schnell) + алиасы из ``business_catalog``."""
    raw = (model_id or model_label or "").strip().lower().replace("-", "_")
    aliases = {**catalog.image_aliases, "fluxschnell": "flux_schnell"}
    return aliases.get(raw, raw)


async def _generate_photo_result(model_key: str, prompt: str) -> GeminiImageResult | str:
    """Возвращает GeminiImageResult (url/bytes) или прямой URL строки (Replicate)."""
    try:
        if model_key == "imagen4":
            return await generate_imagen_fast(prompt)

        if model_key == "flux_schnell":
            if not replicate_configured():
                raise ExternalApiError("Replicate", "REPLICATE_API_TOKEN не задан")
            prompt_en = await enhance_video_prompt_for_replicate(app_settings, prompt)
            url = await call_replicate_model(
                "black-forest-labs/flux-schnell",
                {
                    "prompt": prompt_en,
                    "aspect_ratio": "1:1",
                    "output_format": "webp",
                    "output_quality": 90,
                },
            )
            if not url:
                raise ExternalApiError("Replicate", "Flux Schnell: пустой URL")
            return url

        if model_key == "gpt_image2":
            if not replicate_configured():
                raise ExternalApiError("Replicate", "REPLICATE_API_TOKEN не задан")
            url = await call_replicate_model(
                "openai/dall-e-3",
                {"prompt": prompt, "size": "1024x1024", "quality": "standard", "n": 1},
            )
            if not url:
                raise ExternalApiError("Replicate", "DALL-E 3: пустой URL")
            return url

        if model_key == "nano_banana2":
            return await generate_gemini_image_model(prompt, "gemini-3.1-flash-image-preview")

        if model_key == "nano_banana_pro":
            return await generate_gemini_image_model(prompt, "gemini-3-pro-image-preview")

        raise RuntimeError(f"Неизвестная модель изображения: {model_key}")
    except ExternalApiError:
        raise
    except Exception as exc:
        provider = "Gemini" if model_key in ("imagen4", "nano_banana2", "nano_banana_pro") else "Replicate"
        raise wrap_http_error(provider, exc) from exc


async def _send_generated_photo(
    task: GenTask,
    *,
    photo_url: str | None,
    photo_bytes: bytes | None,
) -> None:
    bot, chat_id = task.bot, task.chat_id
    display = task.model_label or task.image_model_id or "модель"
    caption = (
        f"🎨 **Ваше изображение успешно сгенерировано!**\n"
        f"🤖 Модель: {display}\n"
        f"💎 Стоимость: {task.charged_crystals} 💎"
    )
    markup = result_photo_keyboard(task_id=task.task_id)
    if photo_url:
        sent = await bot.send_photo(
            chat_id,
            photo=photo_url,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
        )
    elif photo_bytes:
        sent = await bot.send_photo(
            chat_id,
            photo=BufferedInputFile(photo_bytes, filename="neuromule_generated.jpg"),
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup,
        )
    else:
        raise RuntimeError("Нет URL и байтов изображения")

    # Кэшируем file_id наибольшего размера + оригинальный URL (если был):
    # TG-канал отправим file_id'ом без скачивания, VK/MAX — оригинальный URL.
    tg_file_id = sent.photo[-1].file_id if sent.photo else None
    _remember_share(task, file_id=tg_file_id, media_url=photo_url)


async def _photo_stub_worker(task: GenTask) -> None:
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    user_prompt = (task.prompt or "").strip()
    if not user_prompt:
        task.status = "failed"
        return

    model_key = _normalize_photo_model_id(task.image_model_id, task.model_label)

    try:
        logger.info(
            "photo job %s user_id=%s model_id=%s model_key=%s prompt_len=%s",
            task.task_id,
            user_id,
            task.image_model_id,
            model_key,
            len(user_prompt),
        )
        async with chat_action_loop(bot, chat_id, "upload_photo"):
            raw = await _generate_photo_result(model_key, user_prompt)
            photo_url: str | None = None
            photo_bytes: bytes | None = None
            if isinstance(raw, str):
                photo_url = raw
            elif isinstance(raw, GeminiImageResult):
                photo_url = raw.url
                photo_bytes = raw.data
            await _send_generated_photo(task, photo_url=photo_url, photo_bytes=photo_bytes)
        task.status = "completed"
    except Exception as exc:
        logger.exception("photo job failed task_id=%s model_key=%s", task.task_id, model_key)
        await fail_generation_task(
            task,
            user_message=msg.TXT_GEN_JOB_FAILED,
            log_msg=f"photo: {exc}",
        )


async def _video_stub_worker(task: GenTask) -> None:
    """PRO-видео: Replicate + перевод промпта; refund через billing_charges."""
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    prompt_ru = (task.prompt or "").strip() or "Кинематографичная сцена, мягкий свет"
    scenario_id = (task.scenario_id or "video_pro_5sec").strip()
    spec = VIDEO_SCENARIOS.get(scenario_id)

    try:
        logger.info(
            "video job %s user_id=%s scenario=%s replicate=%s",
            task.task_id,
            user_id,
            scenario_id,
            replicate_configured(),
        )
        async with chat_action_loop(bot, chat_id, "upload_video"):
            row = await get_user_row(user_id)
            video_url: str | None = None
            if replicate_configured():
                prompt_en = await enhance_video_prompt_for_replicate(
                    app_settings, prompt_ru
                )
                logger.info(
                    "video prompt enhanced task_id=%s len_ru=%s len_en=%s",
                    task.task_id,
                    len(prompt_ru),
                    len(prompt_en),
                )
                model = (spec.replicate_model if spec else None) or settings.replicate_video_model
                inputs: dict = {"prompt": prompt_en, "aspect_ratio": "16:9"}
                if task.file_id and spec and spec.needs_face:
                    image_url = await telegram_photo_download_url(bot, task.file_id)
                    inputs["start_image_url"] = image_url
                # Жёсткий таймаут на Replicate — иначе зависший прокси
                # лочит воркер навсегда, кошелёк юзера в подвешенном виде.
                async with asyncio.timeout(EXTERNAL_API_TIMEOUT_SEC):
                    video_url = await call_replicate_model(model, inputs)

            title = spec.title_ru if spec else "PRO-видео"
            if video_url:
                caption = (
                    f"🎬 <b>{title}</b>\n"
                    "───────────────────\n"
                    f"💎 Списано: <code>{task.charged_crystals} 💎</code>\n"
                    f"🔋 Твой остаток: <code>{row.crystals} 💎</code>"
                )
                caption += _balance_footer(row.crystals)
                sent = await bot.send_video(
                    chat_id,
                    video=video_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=result_video_keyboard_pro(task_id=task.task_id),
                )
                # Кэш для шеринга: file_id → TG-канал, media_url → VK/MAX App.
                tg_file_id = sent.video.file_id if sent.video else None
                _remember_share(task, file_id=tg_file_id, media_url=video_url)
            elif not replicate_configured():
                await asyncio.sleep(4.0)
                cap = f"🎬 {title} (демо: задайте REPLICATE_API_TOKEN)"
                cap += _balance_footer(row.crystals)
                await bot.send_message(
                    chat_id, cap, reply_markup=result_video_keyboard_pro(task_id=task.task_id)
                )
            else:
                raise RuntimeError("Replicate returned empty video URL")

        task.status = "completed"
    except Exception as exc:
        await fail_generation_task(
            task,
            user_message=msg.TXT_VIDEO_REPLICATE_FAILED,
            log_msg=f"video: {exc}",
        )


async def _build_music_cover(style_prompt: str) -> InputFile | None:
    """Параллельно с Suno генерируем квадратную ИИ-обложку под трек.

    Возвращает готовый ``InputFile`` для ``send_audio(thumbnail=...)`` или
    ``None`` при любой ошибке — отсутствие обложки никогда не должно
    ломать выдачу самого трека.
    """

    try:
        cover_prompt = (
            "Square album cover artwork for a song. Style and mood: "
            f"{style_prompt}. Bold composition, premium high-fidelity studio "
            "aesthetic, vibrant cinematic colors, no text, no watermark, "
            "centered subject, vinyl-ready."
        )
        result = await generate_imagen_fast(cover_prompt)
    except Exception:
        logger.info("music cover: imagen failed, fallback to no-thumbnail", exc_info=True)
        return None

    if result.url:
        return URLInputFile(result.url, filename="neuromule_cover.jpg")
    if result.data:
        return BufferedInputFile(result.data, filename="neuromule_cover.jpg")
    return None


def _format_music_caption(style: str, balance: int, cost: int) -> str:
    caption = msg.TXT_RESULT_MUSIC_CAPTION.format(
        style=style[:120],
        balance=balance,
        cost=cost,
    )
    caption += _balance_footer(balance)
    return caption


async def _music_stub_worker(task: GenTask) -> None:
    """Музыка Suno AI v4 + ИИ-обложка Imagen 4 + апсейл-клавиатура.

    Поток:
        1. ``record_voice`` chat-action 24/7 пока крутится рендер.
        2. Prompt enhancer (RU → EN + ``cinematic mix, high fidelity,
           tight production``) ради стабильного качества Suno.
        3. Параллельно: ``generate_music_track`` + ``generate_imagen_fast``.
        4. ``send_audio`` с ``performer="NeuroMule 🐎"`` и ``thumbnail``.
        5. ``result_music_keyboard_pro`` для апсейла (клип/extend/clone/publish).
        6. Запоминаем ``clip_id`` для будущего «Продлить трек».

    При любой ошибке Suno (``None`` от ``generate_music_track``) — рефанд
    15 💎 через ``fail_generation_task`` + ``TXT_MUSIC_SUNO_FAILED``.
    """

    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    raw_style = (task.prompt or "").strip()[:500] or "по запросу"

    try:
        logger.info(
            "music job %s style=%r lyrics=%s instrumental=%s suno=%s",
            task.task_id,
            raw_style[:120],
            bool(task.music_lyrics),
            task.music_instrumental,
            suno_configured(),
        )

        async with chat_action_loop(bot, chat_id, "record_voice"):
            row = await get_user_row(user_id)
            cost = task.charged_crystals or settings.cost_music

            enhanced_style = await enhance_music_style_prompt(app_settings, raw_style)

            track: SunoTrack | None = None
            cover: InputFile | None = None

            if suno_configured():
                track_coro = generate_music_track(
                    enhanced_style,
                    lyrics=task.music_lyrics,
                    make_instrumental=task.music_instrumental,
                    continue_clip_id=task.music_continue_clip_id,
                )
                cover_coro = _build_music_cover(raw_style)
                # Suno иногда «зависает» на польном rendering 3+ минут.
                # Жёстко закрываем по таймауту — иначе очередь стопорится.
                async with asyncio.timeout(EXTERNAL_API_TIMEOUT_SEC):
                    track, cover = await asyncio.gather(
                        track_coro, cover_coro, return_exceptions=False
                    )

            caption = _format_music_caption(raw_style, row.crystals, cost)

            if track:
                last_music_request.remember(
                    user_id,
                    style=raw_style,
                    lyrics=task.music_lyrics,
                    make_instrumental=task.music_instrumental,
                    clip_id=track.clip_id,
                )
                send_kwargs: dict = {
                    "audio": track.audio_url,
                    "title": track.title,
                    "performer": "NeuroMule 🐎",
                    "caption": caption,
                    "parse_mode": ParseMode.HTML,
                    "reply_markup": result_music_keyboard_pro(task_id=task.task_id),
                }
                if cover is not None:
                    send_kwargs["thumbnail"] = cover
                sent = await bot.send_audio(chat_id, **send_kwargs)
                # Кэш для шеринга: file_id Telegram + audio_url Suno
                # (audio_url нужен VK/MAX, file_id — TG-каналу Галереи).
                tg_file_id = sent.audio.file_id if sent.audio else None
                _remember_share(task, file_id=tg_file_id, media_url=track.audio_url)
            elif not suno_configured():
                await asyncio.sleep(2.0)
                cap = caption + "\n\n<i>(демо: задайте SUNO_API_TOKEN и URL прокси)</i>"
                await bot.send_message(
                    chat_id,
                    cap,
                    parse_mode=ParseMode.HTML,
                    reply_markup=result_music_keyboard_pro(task_id=task.task_id),
                )
            else:
                raise RuntimeError("Suno returned empty audio URL")

        task.status = "completed"
    except Exception as exc:
        await fail_generation_task(
            task,
            user_message=msg.TXT_MUSIC_SUNO_FAILED,
            log_msg=f"music: {exc}",
        )


async def _animate_stub_worker(task: GenTask) -> None:
    """
    Воркер очереди для оживления фото.
    Использует Telegram file_id исходного снимка из task.file_id.
    """
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    file_id = (task.file_id or "").strip()
    if not file_id:
        logger.error("animate job %s: missing file_id user_id=%s", task.task_id, user_id)
        await fail_generation_task(
            task,
            user_message=msg.TXT_ANIMATE_FAILED,
            log_msg="animate: missing file_id",
        )
        return

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
                async with asyncio.timeout(EXTERNAL_API_TIMEOUT_SEC):
                    animated_url = await call_replicate_model(
                        settings.replicate_animate_model, inputs
                    )

            if animated_url:
                cap = msg.TXT_ANIMATE_SUCCESS
                cap += "\n\n" + msg.TXT_RESULT_ANIMATE_CAPTION.format(
                    cost=settings.cost_animate,
                    balance=row.crystals,
                )
                cap += _balance_footer(row.crystals)
                sent = await bot.send_video(chat_id, video=animated_url, caption=cap)
                # Кэш для шеринга оживления (animate ~ video в VK/MAX).
                tg_file_id = sent.video.file_id if sent.video else None
                _remember_share(task, file_id=tg_file_id, media_url=animated_url)
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
        await fail_generation_task(
            task,
            user_message=msg.TXT_ANIMATE_REPLICATE_FAILED,
            log_msg=f"animate: {exc}",
        )


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
    image_model_id: str,
    model_label: str,
    user_prompt: str,
    used_daily_slot: bool,
    charged_crystals: int,
    priority: int = 2,
    billing_charge_id: str = "",
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
            image_model_id=image_model_id,
            model_label=model_label,
            used_daily_slot=used_daily_slot,
            charged_crystals=charged_crystals,
            billing_charge_id=billing_charge_id,
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
