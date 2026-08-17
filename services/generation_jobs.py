"""Асинхронные задачи генерации медиа (фото, видео, музыка, оживление).

Поток данных: use-case → fire_*_job → очередь → воркеры.
Ключи: ``REPLICATE_API_TOKEN``, ``SUNO_API_URL`` (см. ``config.Settings``).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.types import BufferedInputFile, URLInputFile

from config import settings
from content import messages as msg
from content.keyboards import new_result_keyboard
from content.inline_keyboards import (
    result_music_keyboard,
    result_music_keyboard_pro,
)
from content.video_menu import result_video_keyboard_pro
from platforms.telegram_chat_action import chat_action_loop
from services import last_music_request, last_share_media
from services.gemini_image_client import (
    GeminiImageResult,
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
from services.photo_aspect_ratio import (
    normalize_photo_aspect_ratio,
    openrouter_aspect_ratio,
)
from services.photo_edit_session import persist_photo_edit_session, save_photo_edit_session
from services.billing.image_pipeline import FREE_PHOTO_MODEL_KEY, normalize_image_model, free_tier_image_model
from services.free_image_cascade import FreeImageCascadeExhausted, generate_free_tier_image
from services.openrouter_images import (
    resolve_composite_refine_fallbacks,
    GPT_IMAGE2_FALLBACKS,
    NANO_BANANO2_FALLBACKS,
    NANO_BANANO_PRO_FALLBACKS,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_FLUX_STACK_FALLBACKS,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_NANO_BANANA2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
    generate_openrouter_composite_photo,
    generate_openrouter_multi_ref_group_photo,
    generate_openrouter_photo,
    openrouter_images_configured,
    resolve_composite_refine_model_key,
    resolve_reference_to_png_data_url,
)
from services.pollinations_client import generate_flux_schnell_image
from services.repository import get_user_row

from services.tariffs import TariffName, normalize_tariff

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

JobKind = Literal["photo", "video", "music", "animate"]
TaskStatus = Literal["pending", "processing", "completed", "failed"]
PlatformKind = Literal["telegram", "vk"]

# Жёсткий потолок ожидания ответа от внешних API (Replicate / Suno / Gemini).
# 180 секунд — это верхняя граница для длинного Replicate-видео (5-10 сек
# конечного клипа рендерится 1-3 минуты). Дальше — таймаут и автоматический
# refund списанных Кристаллов через ``fail_generation_task``.
EXTERNAL_API_TIMEOUT_SEC: int = 180
# Flux FREE: Pollinations + cascade — не держим status_msg дольше 3 минут.
FREE_PHOTO_HARD_TIMEOUT_SEC: int = 180


@dataclass
class GenTask:
    """Задание в очереди генерации медиа."""

    task_id: str
    bot: "Bot | None"
    chat_id: int
    user_id: int
    task_type: JobKind
    platform: PlatformKind = "telegram"
    status: str = "pending"
    prompt: str | None = None
    file_id: str | None = None
    reference_image_url: str | None = None
    reference_image_bytes: bytes | None = None
    reference_mime: str = "image/jpeg"
    aspect_ratio: str = "1:1"
    image_model_id: str = ""
    model_label: str = ""
    scenario_id: str = ""
    used_daily_slot: bool = False
    charged_crystals: int = 0
    billing_charge_id: str = ""
    status_message_id: int | None = None
    music_lyrics: str | None = None
    music_instrumental: bool = False
    music_continue_clip_id: str | None = None
    generation_seed: int | None = None
    cleanup_message_ids: tuple[int, ...] = ()
    composite_refine: bool = False
    composite_base_file_id: str | None = None
    composite_base_reference_url: str | None = None
    composite_base_reference_bytes: bytes | None = None
    group_multi_ref: bool = False
    group_ref_file_ids: tuple[str, ...] = ()

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
    """Канонический ключ модели из меню / FSM / legacy callback."""
    return normalize_image_model(model_id or model_label)


_TEXT_DESIGN_INTENT_KEYWORDS: tuple[str, ...] = (
    "архитектур",
    "architecture",
    "дизайн",
    "design",
    "логотип",
    "logo",
    "typography",
    "типограф",
    "текст на",
    "text on",
    "надпись",
    "poster",
    "infographic",
    "blueprint",
    "interior",
    "building",
    "чертёж",
    "чертеж",
    "макет",
)


def _photo_has_reference(
    file_id: str | None,
    reference_image_url: str | None,
    reference_image_bytes: bytes | None,
) -> bool:
    if reference_image_bytes:
        return True
    if (reference_image_url or "").strip():
        return True
    return bool((file_id or "").strip())


def _is_text_design_intent(prompt: str) -> bool:
    low = (prompt or "").strip().lower()
    if not low:
        return False
    return any(keyword in low for keyword in _TEXT_DESIGN_INTENT_KEYWORDS)


def resolve_smart_photo_model_key(
    model_key: str,
    *,
    has_reference: bool,
    prompt: str,
) -> str:
    """
    Chatcom-style роутинг: селфи → identity-модель по выбору пользователя;
    GPT Image 2 и Nano сохраняют свой стек; прочие модели → nano_banana_pro.
    """
    key = normalize_image_model(model_key)
    if has_reference:
        if key in ("gpt_image_2", "nano_banana_2", "nano_banana_pro"):
            return key
        if key != "nano_banana_pro":
            logger.info(
                "smart photo routing: model=%s + reference → nano_banana_pro",
                key,
            )
            return "nano_banana_pro"
        return key

    if key in ("nano_banana_2", "nano_banana_pro"):
        if _is_text_design_intent(prompt):
            logger.info(
                "smart photo routing: model=%s text/design intent → flux_2_pro",
                key,
            )
            return "flux_2_pro"
        logger.info(
            "smart photo routing: model=%s t2i without reference → flux_2_pro",
            key,
        )
        return "flux_2_pro"

    return key


async def _load_reference_image_bytes(
    *,
    bot: "Bot | None",
    file_id: str | None,
    reference_image_url: str | None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> tuple[bytes, str]:
    """Telegram file_id, HTTPS-URL или готовые bytes → bytes + mime для i2i."""
    if reference_image_bytes is not None:
        if isinstance(reference_image_bytes, memoryview):
            raw = reference_image_bytes.tobytes()
        elif isinstance(reference_image_bytes, (bytes, bytearray)):
            raw = bytes(reference_image_bytes)
        else:
            raise ExternalApiError("PhotoRef", "reference_image_bytes must be bytes")
        if not raw:
            raise ExternalApiError("PhotoRef", "reference_image_bytes is empty")
        return raw, reference_mime or "image/jpeg"

    url = (reference_image_url or "").strip()
    if url:
        import httpx

        from services.streaming_download import stream_download_to_bytes

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            data = await stream_download_to_bytes(client, url, source="photo_ref_url")
        if not data:
            raise ExternalApiError("PhotoRef", "не удалось скачать reference_image_url")
        mime = "image/jpeg"
        low = url.lower()
        if low.endswith(".png"):
            mime = "image/png"
        elif low.endswith(".webp"):
            mime = "image/webp"
        return data, mime

    fid = (file_id or "").strip()
    if fid and bot is not None:
        return await _load_telegram_photo_bytes(bot, fid)

    raise ExternalApiError("PhotoRef", "нет file_id или reference_image_url")


async def _load_telegram_photo_bytes(bot: "Bot", file_id: str) -> tuple[bytes, str]:
    """Скачивает фото из Telegram для image-to-image."""
    from io import BytesIO

    file = await bot.get_file(file_id)
    if not file.file_path:
        raise ExternalApiError("Telegram", "file_path отсутствует")
    buf = BytesIO()
    await bot.download_file(file.file_path, buf)
    data = buf.getvalue()
    if not data:
        raise ExternalApiError("Telegram", "пустой файл фото")
    path = file.file_path.lower()
    mime = "image/jpeg"
    if path.endswith(".png"):
        mime = "image/png"
    elif path.endswith(".webp"):
        mime = "image/webp"
    return data, mime


async def _resolve_reference_data_url(
    bot: "Bot | None",
    file_id: str | None,
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> str | None:
    """Референс → PNG data-URL для OpenRouter (скачивание через bot, без TG https URL)."""
    if not _photo_has_reference(file_id, reference_image_url, reference_image_bytes):
        return None

    ref_bytes, _ref_mime = await _load_reference_image_bytes(
        bot=bot,
        file_id=file_id,
        reference_image_url=reference_image_url,
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
    )
    from services.openrouter_images import reference_bytes_to_png_data_url

    png_data_url = reference_bytes_to_png_data_url(ref_bytes)
    logger.info(
        "reference resolved via bot download bytes=%s png_len=%s",
        len(ref_bytes),
        len(png_data_url),
    )
    return png_data_url


async def _reference_image_data_url(
    *,
    bot: "Bot | None",
    file_id: str | None,
    reference_image_url: str | None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> str:
    """Backward-compatible alias → ``_resolve_reference_data_url``."""
    url = await _resolve_reference_data_url(
        bot=bot,
        file_id=file_id,
        reference_image_url=reference_image_url,
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
    )
    if not url:
        raise ExternalApiError("PhotoRef", "нет file_id или reference_image_url")
    return url


async def _download_result_image_bytes(image_url: str) -> bytes | None:
    """Скачать результат OpenRouter на VDSina — Telegram часто не тянет внешний CDN."""
    url = (image_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    import httpx

    from services.streaming_download import stream_download_to_bytes

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
        return await stream_download_to_bytes(client, url, source="openrouter_result")


async def _telegram_photo_data_url(bot: "Bot", file_id: str) -> str:
    """Legacy alias: Telegram file_id → https URL для OpenRouter."""
    from services.openrouter_images import resolve_openrouter_reference_url

    url = await resolve_openrouter_reference_url(bot=bot, file_id=file_id)
    if not url:
        raise ExternalApiError("Telegram", "empty reference URL")
    return url


async def _safe_delete_status_message(task: GenTask) -> None:
    """Убрать status_msg после успешной доставки фото."""
    if task.platform == "vk" or task.bot is None:
        return
    msg_id = task.status_message_id
    if msg_id is None:
        return
    try:
        await task.bot.delete_message(chat_id=task.chat_id, message_id=int(msg_id))
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
        logger.debug("status delete skipped task=%s: %s", task.task_id, exc)
    except Exception:
        logger.warning("status delete unexpected task=%s", task.task_id, exc_info=True)
    finally:
        task.status_message_id = None


async def _safe_delete_cleanup_messages(task: GenTask) -> None:
    """Удалить промежуточные сервисные сообщения (zero-trash UX)."""
    if task.platform == "vk" or task.bot is None:
        return
    for raw_id in task.cleanup_message_ids:
        if raw_id is None:
            continue
        try:
            await task.bot.delete_message(chat_id=task.chat_id, message_id=int(raw_id))
        except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
            logger.debug("cleanup delete skipped task=%s msg=%s: %s", task.task_id, raw_id, exc)
        except Exception:
            logger.warning("cleanup delete unexpected task=%s msg=%s", task.task_id, raw_id, exc_info=True)
    task.cleanup_message_ids = ()


async def _deliver_photo_url_chatcom(task: GenTask, final_image_url: str) -> None:
    """@chatcom UX: document(HD) + photo с клавиатурой — только URL, без байтов на VDSina."""
    bot, chat_id = task.bot, task.chat_id
    if bot is None:
        raise RuntimeError("Telegram photo delivery requires bot")

    final_url = (final_image_url or "").strip()
    if not final_url.startswith(("http://", "https://")):
        raise RuntimeError("chatcom delivery requires http(s) URL")

    display = task.model_label or task.image_model_id or "модель"
    caption_html = msg.format_photo_result_caption_html(display, task.prompt or "")

    await bot.send_document(
        chat_id,
        document=final_url,
        caption=msg.TXT_PHOTO_RESULT_DOCUMENT_CAPTION,
    )
    sent = await bot.send_photo(
        chat_id,
        photo=final_url,
        caption=caption_html,
        parse_mode=ParseMode.HTML,
        reply_markup=new_result_keyboard(task_id=task.task_id),
    )

    tg_file_id = sent.photo[-1].file_id if sent.photo else None
    _remember_share(task, file_id=tg_file_id, media_url=final_url)

    await persist_photo_edit_session(
        task.user_id,
        image_model_id=task.image_model_id,
        image_model_label=task.model_label or task.image_model_id,
        aspect_ratio=task.aspect_ratio,
        telegram_file_id=tg_file_id,
        media_url=final_url,
        message_id=sent.message_id,
        chat_id=chat_id,
        platform="telegram",
        user_prompt=task.prompt,
        reference_file_id=task.file_id,
        generation_seed=task.generation_seed,
    )

    await _safe_delete_status_message(task)
    await _safe_delete_cleanup_messages(task)


async def _deliver_photo_bytes_chatcom(
    task: GenTask,
    photo_bytes: bytes,
    *,
    source_url: str | None = None,
) -> None:
    """Fallback: document + photo из байтов (когда провайдер вернул b64_json)."""
    bot, chat_id = task.bot, task.chat_id
    if bot is None:
        raise RuntimeError("Telegram photo delivery requires bot")

    raw = photo_bytes if isinstance(photo_bytes, bytes) else bytes(photo_bytes)
    if not raw:
        raise RuntimeError("empty photo bytes")

    display = task.model_label or task.image_model_id or "модель"
    caption_html = msg.format_photo_result_caption_html(display, task.prompt or "")
    file = BufferedInputFile(raw, filename="neuromule_generated.jpg")

    await bot.send_document(
        chat_id,
        document=file,
        caption=msg.TXT_PHOTO_RESULT_DOCUMENT_CAPTION,
    )
    sent = await bot.send_photo(
        chat_id,
        photo=file,
        caption=caption_html,
        parse_mode=ParseMode.HTML,
        reply_markup=new_result_keyboard(task_id=task.task_id),
    )

    tg_file_id = sent.photo[-1].file_id if sent.photo else None
    _remember_share(task, file_id=tg_file_id, media_url=None)

    await persist_photo_edit_session(
        task.user_id,
        image_model_id=task.image_model_id,
        image_model_label=task.model_label or task.image_model_id,
        aspect_ratio=task.aspect_ratio,
        telegram_file_id=tg_file_id,
        media_url=(source_url or "").strip() or None,
        reference_image_bytes=raw,
        message_id=sent.message_id,
        chat_id=chat_id,
        platform="telegram",
        user_prompt=task.prompt,
        reference_file_id=task.file_id,
        generation_seed=task.generation_seed,
    )

    await _safe_delete_status_message(task)
    await _safe_delete_cleanup_messages(task)


async def _generate_free_tier_photo(
    prompt: str,
    *,
    bot: "Bot | None",
    file_id: str | None,
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
) -> GeminiImageResult:
    """Flux FREE: каскад Pollinations → OpenRouter spare → RR (таймаут снаружи)."""
    if isinstance(prompt, (bytes, bytearray, memoryview)):
        raise ExternalApiError("FreePhoto", "prompt must be str, not image bytes")
    text = str(prompt or "").strip() or "Улучши это фото"
    ref_bytes: bytes | None = None
    ref_mime = "image/jpeg"
    if file_id or reference_image_url or reference_image_bytes:
        logger.info("Flux FREE i2i: Pollinations skip, spare wheel / OR cascade")
        ref_bytes, ref_mime = await _load_reference_image_bytes(
            bot=bot,
            file_id=file_id,
            reference_image_url=reference_image_url,
            reference_image_bytes=reference_image_bytes,
            reference_mime=reference_mime,
        )
    return await generate_free_tier_image(
        text,
        reference_image_bytes=ref_bytes,
        reference_mime=ref_mime,
    )


async def _free_tier_flux_uses_pollinations(user_id: int | None, model_key: str) -> bool:
    """FREE + Flux Schnell → Pollinations (без платных API-ключей)."""
    if model_key != "flux_2_pro" or user_id is None:
        return False
    row = await get_user_row(user_id)
    return normalize_tariff(row.tariff) is TariffName.FREE


async def _generate_flux_schnell_paid(
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    reference_data_url: str | None = None,
) -> GeminiImageResult:
    """Платный Flux: только OpenRouter Images (+ внутренние OR-fallback)."""
    if not openrouter_images_configured(app_settings):
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")
    return await _generate_openrouter_photo_model(
        OPENROUTER_FLUX_PAID_MODEL,
        prompt,
        aspect_ratio=aspect_ratio,
        reference_data_url=reference_data_url,
        fallback_models=OPENROUTER_FLUX_STACK_FALLBACKS,
    )


async def _generate_openrouter_composite_photo_model(
    model_key: str,
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    bot: "Bot | None" = None,
    object_file_id: str | None = None,
    object_reference_url: str | None = None,
    object_reference_bytes: bytes | None = None,
    object_reference_mime: str = "image/jpeg",
    base_file_id: str | None = None,
    base_reference_url: str | None = None,
    base_reference_bytes: bytes | None = None,
    base_reference_mime: str = "image/jpeg",
) -> GeminiImageResult:
    if not openrouter_images_configured(app_settings):
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    or_model = resolve_composite_refine_model_key(model_key)
    base_data_url = await resolve_reference_to_png_data_url(
        bot=bot,
        file_id=base_file_id,
        reference_url=base_reference_url,
        reference_bytes=base_reference_bytes,
        reference_mime=base_reference_mime,
    )
    object_data_url = await resolve_reference_to_png_data_url(
        bot=bot,
        file_id=object_file_id,
        reference_url=object_reference_url,
        reference_bytes=object_reference_bytes,
        reference_mime=object_reference_mime,
    )
    return await generate_openrouter_composite_photo(
        app_settings,
        model=or_model,
        model_key=model_key,
        user_prompt=prompt,
        base_image_data_url=base_data_url,
        object_image_data_url=object_data_url,
        aspect_ratio=openrouter_aspect_ratio(aspect_ratio),
        fallback_models=resolve_composite_refine_fallbacks(model_key),
        timeout_sec=float(EXTERNAL_API_TIMEOUT_SEC),
    )


async def _generate_openrouter_multi_ref_group_model(
    model_key: str,
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    bot: "Bot | None" = None,
    group_ref_file_ids: tuple[str, ...] = (),
) -> GeminiImageResult:
    if not openrouter_images_configured(app_settings):
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    refs = tuple((fid or "").strip() for fid in group_ref_file_ids if (fid or "").strip())
    if len(refs) < 2:
        raise ExternalApiError("OpenRouter", "group multi-ref requires at least 2 references")

    or_model = OPENROUTER_NANO_BANANA_PRO_MODEL
    data_urls: list[str] = []
    for file_id in refs:
        data_urls.append(
            await resolve_reference_to_png_data_url(
                bot=bot,
                file_id=file_id,
            )
        )
    return await generate_openrouter_multi_ref_group_photo(
        app_settings,
        model=or_model,
        user_prompt=prompt,
        reference_image_data_urls=data_urls,
        aspect_ratio=openrouter_aspect_ratio(aspect_ratio),
        timeout_sec=float(EXTERNAL_API_TIMEOUT_SEC),
    )


async def _generate_openrouter_photo_model(
    model: str,
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    reference_data_url: str | None = None,
    fallback_models: tuple[str, ...] = (),
) -> GeminiImageResult:
    """Платные модели через OpenRouter Images.

    При i2i (селфи + intent) ``openrouter_images`` переводит intent на EN,
    склеивает stack-specific prompt (Google identity / OpenAI inpaint / Flux)
    и кодирует референс в PNG base64 для ``input_references``.
    """
    if not openrouter_images_configured(app_settings):
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    return await generate_openrouter_photo(
        app_settings,
        model=model,
        user_prompt=prompt,
        aspect_ratio=openrouter_aspect_ratio(aspect_ratio),
        reference_data_url=reference_data_url,
        fallback_models=fallback_models,
        timeout_sec=float(EXTERNAL_API_TIMEOUT_SEC),
    )


async def _generate_photo_result(
    model_key: str,
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    user_id: int | None = None,
    bot: "Bot | None" = None,
    file_id: str | None = None,
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
    composite_refine: bool = False,
    composite_base_file_id: str | None = None,
    composite_base_reference_url: str | None = None,
    composite_base_reference_bytes: bytes | None = None,
    composite_base_reference_mime: str = "image/jpeg",
    group_multi_ref: bool = False,
    group_ref_file_ids: tuple[str, ...] = (),
) -> GeminiImageResult | str:
    """Возвращает GeminiImageResult (url/bytes) или прямой URL строки."""
    ar = normalize_photo_aspect_ratio(aspect_ratio)
    model_key = normalize_image_model(model_key)

    if group_multi_ref:
        logger.info(
            "group multi-ref routing: model=%s refs=%s",
            model_key,
            len(group_ref_file_ids),
        )
        return await _generate_openrouter_multi_ref_group_model(
            model_key,
            prompt,
            aspect_ratio=ar,
            bot=bot,
            group_ref_file_ids=group_ref_file_ids,
        )

    if composite_refine:
        composite_key = resolve_composite_refine_model_key(model_key)
        logger.info(
            "composite refine routing: model=%s → %s",
            model_key,
            composite_key,
        )
        try:
            return await _generate_openrouter_composite_photo_model(
                model_key,
                prompt,
                aspect_ratio=ar,
                bot=bot,
                object_file_id=file_id,
                object_reference_url=reference_image_url,
                object_reference_bytes=reference_image_bytes,
                object_reference_mime=reference_mime,
                base_file_id=composite_base_file_id,
                base_reference_url=composite_base_reference_url,
                base_reference_bytes=composite_base_reference_bytes,
                base_reference_mime=composite_base_reference_mime,
            )
        except ExternalApiError as exc:
            from services.photo_multi_ref_routing import is_composite_print_intent

            base_id = (composite_base_file_id or "").strip()
            object_id = (file_id or "").strip()
            if is_composite_print_intent(prompt) or not base_id or not object_id:
                raise
            logger.warning(
                "composite failed for group-like prompt, retrying as multi-ref group: %s",
                exc,
            )
            return await _generate_openrouter_multi_ref_group_model(
                model_key,
                prompt,
                aspect_ratio=ar,
                bot=bot,
                group_ref_file_ids=(base_id, object_id),
            )

    has_reference = _photo_has_reference(file_id, reference_image_url, reference_image_bytes)
    model_key = resolve_smart_photo_model_key(
        model_key,
        has_reference=has_reference,
        prompt=prompt,
    )
    reference_data_url = await _resolve_reference_data_url(
        bot,
        file_id,
        reference_image_url,
        reference_image_bytes,
        reference_mime,
    )

    try:
        if model_key == "flux_2_pro":
            if await _free_tier_flux_uses_pollinations(user_id, model_key):
                return await generate_flux_schnell_image(prompt)
            return await _generate_flux_schnell_paid(
                prompt,
                aspect_ratio=ar,
                reference_data_url=reference_data_url,
            )

        if model_key == FREE_PHOTO_MODEL_KEY:
            raise ExternalApiError("FreePhoto", "flux_free requires task worker context")

        if model_key == "gpt_image_2":
            return await _generate_openrouter_photo_model(
                OPENROUTER_GPT_IMAGE2_MODEL,
                prompt,
                aspect_ratio=ar,
                reference_data_url=reference_data_url,
                fallback_models=GPT_IMAGE2_FALLBACKS,
            )

        if model_key == "nano_banana_2":
            return await _generate_openrouter_photo_model(
                OPENROUTER_NANO_BANANA2_MODEL,
                prompt,
                aspect_ratio=ar,
                reference_data_url=reference_data_url,
                fallback_models=NANO_BANANO2_FALLBACKS,
            )

        if model_key == "nano_banana_pro":
            return await _generate_openrouter_photo_model(
                OPENROUTER_NANO_BANANA_PRO_MODEL,
                prompt,
                aspect_ratio=ar,
                reference_data_url=reference_data_url,
                fallback_models=NANO_BANANO_PRO_FALLBACKS,
            )

        raise RuntimeError(f"Неизвестная модель изображения: {model_key}")
    except ExternalApiError:
        raise
    except Exception as exc:
        provider = (
            "OpenRouter"
            if model_key
            in ("nano_banana_2", "nano_banana_pro", "flux_2_pro", "gpt_image_2")
            else "ExternalApi"
        )
        raise wrap_http_error(provider, exc) from exc


async def _send_vk_generated_photo(
    task: GenTask,
    *,
    photo_url: str | None,
    photo_bytes: bytes | None,
) -> None:
    from io import BytesIO
    import random

    import httpx

    from platforms.vk_runtime import get_vk_bot
    from services.streaming_download import stream_download_to_bytes
    from services.vk_api_retry import vk_api_call_with_retry
    from platforms.vk_photo_keyboard import vk_photo_refine_keyboard_json
    from services.vk_plain_text import vk_plain_text

    refine_kb = vk_photo_refine_keyboard_json()

    bot = get_vk_bot()
    if bot is None:
        raise RuntimeError("VK bot is not initialized")

    display = task.model_label or task.image_model_id or "модель"
    if task.used_daily_slot:
        caption = (
            f"🎨 Бесплатное фото дня готово!\n"
            f"🤖 Модель: {display}\n"
            f"💎 Стоимость: 0 💎"
        )
    else:
        caption = (
            f"🎨 Изображение сгенерировано!\n"
            f"🤖 Модель: {display}\n"
            f"💎 Стоимость: {task.charged_crystals} 💎"
        )
    caption = vk_plain_text(caption)

    payload = photo_bytes
    if payload is None and photo_url:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            payload = await stream_download_to_bytes(client, photo_url, source="vk_outbound_photo")
    if not payload:
        raise RuntimeError("Нет байтов изображения для VK")

    from vkbottle import PhotoMessageUploader

    uploader = PhotoMessageUploader(bot.api)

    async def _upload() -> str:
        return await uploader.upload(file_source=BytesIO(payload), peer_id=task.chat_id)

    attachment = await vk_api_call_with_retry(_upload, context="photos.upload")

    async def _send() -> object:
        return await bot.api.messages.send(
            peer_id=task.chat_id,
            message=caption,
            attachment=attachment,
            keyboard=refine_kb,
            random_id=random.randint(1, 2_000_000_000),
        )

    await vk_api_call_with_retry(_send, context="messages.send_photo")
    _remember_share(task, media_url=photo_url)
    save_photo_edit_session(
        task.user_id,
        image_model_id=task.image_model_id,
        image_model_label=task.model_label or task.image_model_id,
        aspect_ratio=task.aspect_ratio,
        media_url=photo_url,
        reference_image_bytes=payload,
        reference_mime="image/jpeg",
        platform="vk",
        chat_id=task.chat_id,
    )


async def _send_generated_photo(
    task: GenTask,
    *,
    photo_url: str | None,
    photo_bytes: bytes | None,
) -> None:
    if task.platform == "vk":
        await _send_vk_generated_photo(task, photo_url=photo_url, photo_bytes=photo_bytes)
        return

    bot, chat_id = task.bot, task.chat_id
    if bot is None:
        raise RuntimeError("Telegram photo delivery requires bot")

    if photo_url and str(photo_url).startswith(("http://", "https://")):
        downloaded = await _download_result_image_bytes(str(photo_url))
        if downloaded:
            await _deliver_photo_bytes_chatcom(task, downloaded, source_url=str(photo_url))
            return
        logger.warning(
            "photo delivery: CDN download failed task=%s, fallback to URL send",
            task.task_id,
        )
        await _deliver_photo_url_chatcom(task, str(photo_url))
        return

    if photo_bytes:
        await _deliver_photo_bytes_chatcom(task, photo_bytes)
        return

    raise RuntimeError("Нет URL и байтов изображения")


async def _photo_stub_worker(task: GenTask) -> None:
    task.status = "processing"
    bot, chat_id, user_id = task.bot, task.chat_id, task.user_id
    user_prompt = (task.prompt or "").strip()
    if not user_prompt:
        task.status = "failed"
        return

    model_key = _normalize_photo_model_id(task.image_model_id, task.model_label)
    is_free = task.used_daily_slot or model_key == FREE_PHOTO_MODEL_KEY

    logger.info(
        "photo job %s user_id=%s model_id=%s model_key=%s prompt_len=%s file_id=%s ref_url=%s ref_bytes=%s free_slot=%s platform=%s",
        task.task_id,
        user_id,
        task.image_model_id,
        model_key,
        len(user_prompt),
        bool(task.file_id),
        bool(task.reference_image_url),
        bool(task.reference_image_bytes),
        task.used_daily_slot,
        task.platform,
    )

    try:
        from contextlib import asynccontextmanager

        from services.photo_gen_status import photo_status_progress_scope

        @asynccontextmanager
        async def _photo_action_scope():
            if task.platform == "vk" or bot is None:
                yield
                return
            async with chat_action_loop(bot, chat_id, "upload_photo"):
                yield

        async with photo_status_progress_scope(
            bot if task.platform == "telegram" else None,
            chat_id,
            task.status_message_id,
            model_label=task.model_label or task.image_model_id,
            aspect_ratio=task.aspect_ratio,
            model_id=task.image_model_id,
            used_daily_slot=task.used_daily_slot,
        ):
            async with _photo_action_scope():
                photo_url: str | None = None
                photo_bytes: bytes | None = None
                raw: GeminiImageResult | str | None = None

                try:
                    if is_free and not task.composite_refine and not task.group_multi_ref:
                        async with asyncio.timeout(FREE_PHOTO_HARD_TIMEOUT_SEC):
                            raw = await _generate_free_tier_photo(
                                user_prompt,
                                bot=bot,
                                file_id=task.file_id,
                                reference_image_url=task.reference_image_url,
                                reference_image_bytes=task.reference_image_bytes,
                                reference_mime=task.reference_mime,
                            )
                    else:
                        raw = await _generate_photo_result(
                            model_key,
                            user_prompt,
                            aspect_ratio=task.aspect_ratio,
                            user_id=user_id,
                            bot=bot,
                            file_id=task.file_id,
                            reference_image_url=task.reference_image_url,
                            reference_image_bytes=task.reference_image_bytes,
                            reference_mime=task.reference_mime,
                            composite_refine=task.composite_refine,
                            composite_base_file_id=task.composite_base_file_id,
                            composite_base_reference_url=task.composite_base_reference_url,
                            composite_base_reference_bytes=task.composite_base_reference_bytes,
                            composite_base_reference_mime=task.reference_mime,
                            group_multi_ref=task.group_multi_ref,
                            group_ref_file_ids=task.group_ref_file_ids,
                        )
                except TimeoutError as err:
                    logger.error(
                        "Критический сбой бесплатного каскада. Мул завис. Ошибка: %s",
                        err,
                    )
                    await fail_generation_task(
                        task,
                        user_message=msg.TXT_FREE_IMAGE_CASCADE_FAILED,
                        log_msg="photo free hard timeout",
                        exc=err,
                    )
                    return

                if raw is not None:
                    if isinstance(raw, str):
                        photo_url = raw
                    elif isinstance(raw, GeminiImageResult):
                        photo_url = raw.url
                        photo_bytes = raw.data

                await _send_generated_photo(task, photo_url=photo_url, photo_bytes=photo_bytes)
        task.status = "completed"
    except FreeImageCascadeExhausted as exc:
        logger.error(
            "Критический сбой бесплатного каскада. Мул завис. Ошибка: %s",
            exc,
        )
        await fail_generation_task(
            task,
            user_message=msg.TXT_FREE_IMAGE_CASCADE_FAILED,
            log_msg="photo cascade exhausted",
            exc=exc,
        )
    except Exception as exc:
        logger.exception("photo job failed task_id=%s model_key=%s", task.task_id, model_key)
        logger.error(
            "Критический сбой бесплатного каскада. Мул завис. Ошибка: %s",
            exc,
        )
        fail_msg = (
            msg.TXT_GROUP_PHOTO_API_FAILED.format(refs_count=len(task.group_ref_file_ids))
            if task.group_multi_ref
            else (
                msg.TXT_PHOTO_COMPOSITE_API_FAILED
                if task.composite_refine
                else (
                    msg.TXT_FREE_IMAGE_CASCADE_FAILED
                    if is_free
                    else msg.TXT_GEN_JOB_FAILED
                )
            )
        )
        await fail_generation_task(
            task,
            user_message=fail_msg,
            log_msg="photo job failed",
            exc=exc,
        )
        if task.composite_refine and task.bot is not None and task.platform == "telegram":
            from content.inline_keyboards import composite_retry_keyboard
            from services.api_resilience import notify_user_safe

            try:
                await notify_user_safe(task.bot, task.chat_id, msg.TXT_COMPOSITE_RETRY_HINT)
                await task.bot.send_message(
                    task.chat_id,
                    "👇",
                    reply_markup=composite_retry_keyboard(),
                )
            except Exception:
                logger.debug("composite retry keyboard send failed", exc_info=True)
    finally:
        # status_msg остаётся на ошибке (отредактирован) или уже удалён на успехе.
        pass


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
            log_msg="video job failed",
            exc=exc,
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
    """Музыка Suno AI v4 + ИИ-обложка + апсейл-клавиатура.

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
            log_msg="music job failed",
            exc=exc,
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
            log_msg="animate job failed",
            exc=exc,
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
    bot: "Bot | None",
    chat_id: int,
    user_id: int,
    image_model_id: str,
    model_label: str,
    user_prompt: str,
    used_daily_slot: bool,
    charged_crystals: int,
    priority: int = 2,
    billing_charge_id: str = "",
    telegram_file_id: str | None = None,
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
    aspect_ratio: str = "1:1",
    status_message_id: int | None = None,
    *,
    platform: PlatformKind = "telegram",
    generation_seed: int | None = None,
    cleanup_message_ids: tuple[int, ...] | list[int] | None = None,
    composite_refine: bool = False,
    composite_base_file_id: str | None = None,
    composite_base_reference_url: str | None = None,
    composite_base_reference_bytes: bytes | None = None,
    group_multi_ref: bool = False,
    group_ref_file_ids: tuple[str, ...] | list[str] | None = None,
) -> None:
    ref_url = (reference_image_url or "").strip() or None
    ref_bytes: bytes | None = None
    if reference_image_bytes is not None:
        if isinstance(reference_image_bytes, memoryview):
            ref_bytes = reference_image_bytes.tobytes()
        elif isinstance(reference_image_bytes, (bytes, bytearray)):
            ref_bytes = bytes(reference_image_bytes)
    if composite_base_reference_bytes is not None:
        if isinstance(composite_base_reference_bytes, memoryview):
            base_bytes = composite_base_reference_bytes.tobytes()
        elif isinstance(composite_base_reference_bytes, (bytes, bytearray)):
            base_bytes = bytes(composite_base_reference_bytes)
        else:
            base_bytes = None
    else:
        base_bytes = None
    group_refs = tuple(
        (fid or "").strip()
        for fid in (group_ref_file_ids or ())
        if (fid or "").strip()
    )
    seed = generation_seed if generation_seed is not None else random.randint(1, 2_000_000_000)
    cleanup_ids = tuple(int(x) for x in (cleanup_message_ids or ()) if x)
    _enqueue(
        priority,
        GenTask(
            task_id=_new_task_id(),
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
            task_type="photo",
            platform=platform,
            prompt=user_prompt,
            file_id=telegram_file_id,
            reference_image_url=ref_url if ref_bytes is None else None,
            reference_image_bytes=ref_bytes,
            reference_mime=reference_mime or "image/jpeg",
            aspect_ratio=normalize_photo_aspect_ratio(aspect_ratio),
            image_model_id=image_model_id,
            model_label=model_label,
            used_daily_slot=used_daily_slot,
            charged_crystals=charged_crystals,
            billing_charge_id=billing_charge_id,
            status_message_id=status_message_id,
            generation_seed=seed,
            cleanup_message_ids=cleanup_ids,
            composite_refine=composite_refine,
            composite_base_file_id=(composite_base_file_id or "").strip() or None,
            composite_base_reference_url=(composite_base_reference_url or "").strip() or None,
            composite_base_reference_bytes=base_bytes,
            group_multi_ref=group_multi_ref,
            group_ref_file_ids=group_refs,
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
