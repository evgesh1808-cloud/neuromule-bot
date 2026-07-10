"""Генерация AI-обложки для конструктора блогера (кнопка «🎨 Создать AI-обложку»)."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import httpx

from config import Settings
from services import blogger_post_cache
from services.ai_text import _chat_headers
from services.blogger_image_prompt import sanitize_blogger_image_prompt_for_imagen
from services.blogger_post_cache import BloggerPostDraft
from services.blogger_post_parser import MISSING_SECTION_PLACEHOLDER
from services.gemini_image_client import GeminiImageResult, generate_imagen_fast

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)

_PROMPT_CLEANER_MODEL = "google/gemini-2.5-flash"

# OpenRouter: image-capable модели (Imagen-class через chat + modalities).
_OPENROUTER_COVER_MODELS: tuple[str, ...] = (
    "google/imagen-3",
    "google/gemini-2.5-flash-image",
    "google/gemini-3.1-flash-image-preview",
)

_OPTIMIZER_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"here(?:'s| is) (?:your )?prompt|output:|final prompt:|"
    r"вот (?:ваш )?промпт|готово[!,.]?|ответ:"
    r")\s*:?\s*",
    re.IGNORECASE,
)

_PROMPT_CLEANER_ROLE = """You are a technical prompt optimizer for Imagen 4 image generation.
Your task is to translate the input to English (if it is in Russian) and completely remove abstract concepts, metaphors, and intangible ideas.

FORBIDDEN: Use words like "symbolizing", "representing", "concept of", "feelings", "success", "future", and any abstract metaphors (for example, "a chart soaring into the sky as a symbol of hope").
ALLOWED: Write only physical, tangible objects, clothing, people, light, camera angle, location, and style.
OUTPUT FORMAT: Output strictly the final English prompt on a single line. No intro text, no "Here is your prompt:".

Example:
Input: "A professional photo representing crypto success and human happiness"
Output: "A professional cinematic photo of a smiling man in a modern office looking at a laptop, neon soft lighting, depth of field, 8k resolution"
"""


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
    """Черновик поста из in-memory кэша (``post_id`` → JSON-секции в ``parsed``).

    При отсутствии ``post_id`` — последний черновик пользователя (аналог сессии в Redis).
    """
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
    raw = (draft.image_prompt or "").strip()
    if not raw or raw == MISSING_SECTION_PLACEHOLDER:
        return None
    return raw


def _strip_optimizer_preamble(text: str) -> str:
    result = (text or "").strip()
    while True:
        cleaned = _OPTIMIZER_PREAMBLE_RE.sub("", result, count=1).strip()
        if cleaned == result:
            break
        result = cleaned
    return result.strip().strip('"').strip("'").strip()


async def clean_blogger_cover_prompt(settings: Settings, raw_prompt: str) -> str:
    """LLM-санитизация промпта: RU→EN, удаление абстракций (``prompt_caching`` в ``ask_ai_messages``)."""
    from services.ai_text import ask_ai_messages

    pre_cleaned = sanitize_blogger_image_prompt_for_imagen(raw_prompt)
    if not settings.openrouter_key:
        return pre_cleaned

    try:
        out = await ask_ai_messages(
            settings,
            [
                {"role": "system", "content": _PROMPT_CLEANER_ROLE},
                {"role": "user", "content": f"Input:\n{pre_cleaned}"},
            ],
            timeout=settings.openrouter_timeout_sec,
            models=[_PROMPT_CLEANER_MODEL],
            temperature=0.2,
            max_tokens=256,
        )
        optimized = _strip_optimizer_preamble(out.get("content") or "")
        if not optimized:
            return pre_cleaned
        return sanitize_blogger_image_prompt_for_imagen(optimized)
    except Exception:
        logger.warning("clean_blogger_cover_prompt failed, using regex fallback", exc_info=True)
        return pre_cleaned


def _decode_data_url(url: str) -> bytes | None:
    if ";base64," not in url:
        return None
    try:
        return base64.b64decode(url.split(";base64,", 1)[1])
    except Exception:
        return None


def _parse_openrouter_image_message(message: dict) -> GeminiImageResult:
    for image in message.get("images") or []:
        if not isinstance(image, dict):
            continue
        url = (image.get("image_url") or {}).get("url")
        if not url:
            continue
        if str(url).startswith(("http://", "https://")):
            return GeminiImageResult(url=str(url))
        data = _decode_data_url(str(url))
        if data:
            return GeminiImageResult(data=data)

    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "image_url":
                continue
            url = (part.get("image_url") or {}).get("url")
            if not url:
                continue
            if str(url).startswith(("http://", "https://")):
                return GeminiImageResult(url=str(url))
            data = _decode_data_url(str(url))
            if data:
                return GeminiImageResult(data=data)

    return GeminiImageResult()


async def _generate_cover_via_openrouter(settings: Settings, prompt: str) -> GeminiImageResult:
    """POST OpenRouter ``/chat/completions`` с ``modalities: [image, text]``."""
    if not settings.openrouter_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    last_error: str | None = None
    async with httpx.AsyncClient(timeout=120.0) as client:
        for model in _OPENROUTER_COVER_MODELS:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": ["image", "text"],
                "max_tokens": 1024,
            }
            try:
                response = await client.post(
                    settings.openrouter_chat_url,
                    headers=_chat_headers(settings),
                    json=payload,
                )
                if response.status_code != 200:
                    last_error = f"{model}: HTTP {response.status_code}"
                    logger.warning(
                        "OpenRouter cover model=%s failed: %s",
                        model,
                        response.text[:400],
                    )
                    continue
                data = response.json()
                message = ((data.get("choices") or [{}])[0]).get("message") or {}
                result = _parse_openrouter_image_message(message)
                if result.has_image():
                    logger.info("blogger cover generated via OpenRouter model=%s", model)
                    return result
                last_error = f"{model}: empty image payload"
            except Exception as exc:
                last_error = f"{model}: {exc}"
                logger.warning("OpenRouter cover model=%s error", model, exc_info=True)

    raise RuntimeError(last_error or "OpenRouter image generation failed")


async def generate_blogger_cover_image(settings: Settings, cleaned_prompt: str) -> GeminiImageResult:
    """Imagen 4: OpenRouter → fallback на прямой Gemini Imagen API."""
    try:
        return await _generate_cover_via_openrouter(settings, cleaned_prompt)
    except Exception:
        logger.warning("OpenRouter cover failed, fallback to Gemini Imagen API", exc_info=True)
    return await generate_imagen_fast(cleaned_prompt)


async def run_blogger_cover_turn(
    settings: Settings,
    *,
    user_id: int,
    draft: BloggerPostDraft,
) -> BloggerCoverResult:
    """Полный цикл: извлечь промпт → проверить баланс → очистить → списать → сгенерировать."""
    from services.billing import refund_charge
    from services.billing.blogger_pipeline import (
        can_afford_blogger_cover,
        spend_blogger_cover,
    )
    from services.god_mode import billing_bypass

    raw_prompt = extract_image_prompt_from_draft(draft)
    if not raw_prompt:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)

    cleaned_prompt = await clean_blogger_cover_prompt(settings, raw_prompt)

    charge_id: str | None = None
    if not billing_bypass(user_id):
        spend = await spend_blogger_cover(user_id)
        if not spend.ok:
            return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)
        charge_id = spend.charge.charge_id if spend.charge else None

    try:
        image = await generate_blogger_cover_image(settings, cleaned_prompt)
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
    callback: CallbackQuery,
    *,
    cleaned_prompt: str,
    image: GeminiImageResult,
    caption_template: str,
) -> None:
    """Отправляет обложку новым сообщением, конструктор не трогает."""
    from aiogram.enums import ParseMode
    from aiogram.types import BufferedInputFile

    if callback.message is None:
        return

    safe_prompt = cleaned_prompt.replace("<", "&lt;").replace(">", "&gt;")
    caption = caption_template.format(prompt=safe_prompt)
    if image.url:
        await callback.message.answer_photo(
            photo=image.url,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return
    if image.data:
        await callback.message.answer_photo(
            photo=BufferedInputFile(image.data, filename="blogger_cover.jpg"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return
    raise RuntimeError("deliver_blogger_cover_photo: no image data")


async def handle_blogger_cover_callback(
    settings: Settings,
    callback: CallbackQuery,
    draft: BloggerPostDraft,
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
    )

    if result.outcome is BloggerCoverOutcome.SUCCESS and result.image and result.cleaned_prompt:
        await deliver_blogger_cover_photo(
            callback,
            cleaned_prompt=result.cleaned_prompt,
            image=result.image,
            caption_template=msg.TXT_BLOGGER_COVER_READY,
        )
        logger.info("blogger cover delivered uid=%s post_id=%s", draft.user_id, draft.post_id)
        return result

    if result.outcome is BloggerCoverOutcome.GENERATION_FAILED and callback.message is not None:
        from aiogram.enums import ParseMode

        await callback.message.answer(msg.TXT_BLOGGER_COVER_FAILED, parse_mode=ParseMode.HTML)

    return result
