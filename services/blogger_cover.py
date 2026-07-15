"""Генерация AI-обложки для конструктора блогера (кнопка «🎨 Создать AI-обложку»)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from config import Settings
from services import blogger_post_cache
from services.blogger_image_prompt import sanitize_blogger_image_prompt_for_imagen
from services.blogger_post_cache import BloggerPostDraft
from services.gemini_image_client import GeminiImageResult

from aiogram.types import Message

if TYPE_CHECKING:
    from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)

# Единственная модель обложек блогера — Flux Schnell (Replicate API, как в photo pipeline).
FLUX_SCHNELL_MODEL_ID = "black-forest-labs/flux-schnell"
BLOGGER_COVER_ASPECT_RATIO = "16:9"


class BloggerCoverOutcome(str, Enum):
    PROMPT_NOT_FOUND = "prompt_not_found"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    FREE_IMAGE_MODEL_BLOCKED = "free_image_model_blocked"
    GENERATION_FAILED = "generation_failed"
    SUCCESS = "success"


@dataclass(frozen=True)
class BloggerCoverResult:
    outcome: BloggerCoverOutcome
    cleaned_prompt: str | None = None
    image: GeminiImageResult | None = None


def resolve_blogger_draft(
    user_id: int,
    post_id: str | None = None,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> BloggerPostDraft | None:
    """Черновик поста из in-memory кэша (``post_id`` → JSON-секции в ``parsed``)."""
    if post_id:
        draft = blogger_post_cache.get(post_id, user_id)
        if draft is not None:
            return draft
    if chat_id is not None and message_id is not None:
        draft = blogger_post_cache.get_by_message(chat_id, message_id, user_id)
        if draft is not None:
            return draft
    return blogger_post_cache.get_last(user_id)


def extract_image_prompt_from_draft(draft: BloggerPostDraft) -> str | None:
    """Секция ``===ПРОМПТ ДЛЯ КАРТИНКИ===`` из кэша черновика."""
    from services.blogger_post_parser import extract_blogger_image_prompt

    return extract_blogger_image_prompt(draft.raw_text, draft.parsed)


def prepare_blogger_flux_prompt(raw_prompt: str, *, with_face: bool = False) -> str:
    """Английский промпт для Flux из ``===ПРОМПТ ДЛЯ КАРТИНКИ===`` — только regex, без LLM."""
    cleaned = sanitize_blogger_image_prompt_for_imagen(raw_prompt)
    if with_face and "person" not in cleaned.lower():
        cleaned = cleaned.replace(
            "A professional cinematic photo of ",
            "A professional cinematic portrait photo of a person, ",
            1,
        )
    return cleaned


async def generate_blogger_cover_image(
    settings: Settings,
    cleaned_prompt: str,
    *,
    face_file_id: str | None = None,
    bot: Any | None = None,
) -> GeminiImageResult:
    """Flux Schnell через Replicate; при наличии фото лица — face-swap поверх сцены."""
    from services.replicate_client import (
        call_replicate_model,
        replicate_configured,
        telegram_photo_download_url,
    )

    if not replicate_configured():
        raise RuntimeError("REPLICATE_API_TOKEN is not configured")

    url = await call_replicate_model(
        FLUX_SCHNELL_MODEL_ID,
        {
            "prompt": cleaned_prompt,
            "aspect_ratio": BLOGGER_COVER_ASPECT_RATIO,
            "output_format": "webp",
            "output_quality": 90,
        },
    )
    if not url:
        raise RuntimeError("Flux Schnell: empty URL")

    face_id = (face_file_id or "").strip()
    if face_id and bot is not None:
        try:
            face_url = await telegram_photo_download_url(bot, face_id)
            swapped_url = await call_replicate_model(
                settings.replicate_blogger_face_swap_model,
                {
                    "input_image": url,
                    "swap_image": face_url,
                },
            )
            if swapped_url:
                logger.info(
                    "blogger cover face swap ok model=%s",
                    settings.replicate_blogger_face_swap_model,
                )
                return GeminiImageResult(url=swapped_url)
            logger.warning("blogger cover face swap returned empty url uid_face=%s", face_id[:8])
        except Exception:
            logger.exception("blogger cover face swap failed, using flux base image")

    logger.info("blogger cover generated via Flux Schnell model=%s", FLUX_SCHNELL_MODEL_ID)
    return GeminiImageResult(url=url)


async def run_blogger_cover_turn(
    settings: Settings,
    *,
    user_id: int,
    draft: BloggerPostDraft,
    use_face: bool = False,
    bot: Any | None = None,
) -> BloggerCoverResult:
    """Полный цикл: промпт → биллинг Flux → генерация."""
    from services.billing import refund_charge
    from services.billing.blogger_pipeline import (
        can_afford_blogger_cover,
        spend_blogger_cover,
    )
    from services.god_mode import billing_bypass
    from services.repository import get_blogger_face_file_id

    raw_prompt = extract_image_prompt_from_draft(draft)
    if not raw_prompt:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)

    face_file_id: str | None = None
    if use_face:
        face_file_id = await get_blogger_face_file_id(user_id)

    cleaned_prompt = prepare_blogger_flux_prompt(raw_prompt, with_face=bool(face_file_id))

    charge_id: str | None = None
    if not billing_bypass(user_id):
        spend = await spend_blogger_cover(user_id)
        if not spend.ok:
            if spend.error == "daily_limit_exceeded":
                return BloggerCoverResult(outcome=BloggerCoverOutcome.DAILY_LIMIT_EXCEEDED)
            if spend.error == "free_image_model_blocked":
                return BloggerCoverResult(outcome=BloggerCoverOutcome.FREE_IMAGE_MODEL_BLOCKED)
            return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)
        charge_id = spend.charge.charge_id if spend.charge else None

    try:
        image = await generate_blogger_cover_image(
            settings,
            cleaned_prompt,
            face_file_id=face_file_id,
            bot=bot,
        )
        if not image.has_image():
            raise RuntimeError("empty image result")
        return BloggerCoverResult(
            outcome=BloggerCoverOutcome.SUCCESS,
            cleaned_prompt=cleaned_prompt,
            image=image,
        )
    except Exception:
        if charge_id:
            await refund_charge(charge_id)
        logger.exception("blogger cover generation failed uid=%s post_id=%s", user_id, draft.post_id)
        return BloggerCoverResult(
            outcome=BloggerCoverOutcome.GENERATION_FAILED,
            cleaned_prompt=cleaned_prompt,
        )


async def deliver_blogger_cover_photo(
    message: Message,
    *,
    cleaned_prompt: str,
    image: GeminiImageResult,
    caption_template: str,
) -> None:
    """Отправляет обложку новым сообщением, конструктор не трогает."""
    from aiogram.enums import ParseMode
    from aiogram.types import BufferedInputFile

    safe_prompt = cleaned_prompt.replace("<", "&lt;").replace(">", "&gt;")
    caption = caption_template.format(prompt=safe_prompt)
    if image.url:
        await message.answer_photo(
            photo=image.url,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return
    if image.data:
        await message.answer_photo(
            photo=BufferedInputFile(image.data, filename="blogger_cover.webp"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return
    raise RuntimeError("deliver_blogger_cover_photo: no image data")


async def deliver_blogger_cover_turn_result(
    message: Message,
    result: BloggerCoverResult,
    *,
    draft: BloggerPostDraft,
) -> None:
    """Показывает итог генерации обложки (лимит / успех / ошибка)."""
    from content import messages as msg

    if result.outcome is BloggerCoverOutcome.DAILY_LIMIT_EXCEEDED:
        from aiogram.enums import ParseMode

        await message.answer(msg.TXT_PHOTO_DAILY_LIMIT, parse_mode=ParseMode.HTML)
        return

    if result.outcome is BloggerCoverOutcome.SUCCESS and result.image and result.cleaned_prompt:
        await deliver_blogger_cover_photo(
            message,
            cleaned_prompt=result.cleaned_prompt,
            image=result.image,
            caption_template=msg.TXT_BLOGGER_COVER_READY,
        )
        logger.info("blogger cover delivered uid=%s post_id=%s", draft.user_id, draft.post_id)
        return

    if result.outcome is BloggerCoverOutcome.GENERATION_FAILED:
        from aiogram.enums import ParseMode

        await message.answer(msg.TXT_BLOGGER_COVER_FAILED, parse_mode=ParseMode.HTML)
        return

    if result.outcome is BloggerCoverOutcome.PROMPT_NOT_FOUND:
        from aiogram.enums import ParseMode

        await message.answer(msg.TXT_BLOGGER_IMAGE_PROMPT_NOT_FOUND, parse_mode=ParseMode.HTML)
        return

    if result.outcome is BloggerCoverOutcome.INSUFFICIENT_BALANCE:
        from aiogram.enums import ParseMode

        await message.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, parse_mode=ParseMode.HTML)
        return

    if result.outcome is BloggerCoverOutcome.FREE_IMAGE_MODEL_BLOCKED:
        from aiogram.enums import ParseMode

        await message.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, parse_mode=ParseMode.HTML)


async def handle_blogger_cover_callback(
    settings: Settings,
    callback: CallbackQuery,
    draft: BloggerPostDraft,
    *,
    use_face: bool = False,
) -> BloggerCoverResult:
    """UX-обёртка для callback-кнопки обложки."""
    from content import messages as msg

    if callback.message is None:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    raw_prompt = extract_image_prompt_from_draft(draft)
    if not raw_prompt:
        await callback.answer(msg.TXT_BLOGGER_IMAGE_PROMPT_NOT_FOUND, show_alert=True)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    user_id = callback.from_user.id if callback.from_user else draft.user_id
    from services.billing.blogger_pipeline import can_afford_blogger_cover
    from services.god_mode import billing_bypass

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        await callback.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, show_alert=True)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)

    await callback.answer(msg.TXT_BLOGGER_COVER_GENERATING)

    result = await run_blogger_cover_turn(
        settings,
        user_id=user_id,
        draft=draft,
        use_face=use_face,
        bot=callback.message.bot,
    )

    if callback.message is not None:
        await deliver_blogger_cover_turn_result(callback.message, result, draft=draft)

    return result
