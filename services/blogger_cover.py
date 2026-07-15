"""AI-обложки блогера через OpenRouter Images API (Flux.2 Pro).

Replicate / face-swap здесь не используются.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx
from aiogram.types import Message

from config import Settings
from services import blogger_post_cache
from services.blogger_image_prompt import sanitize_blogger_image_prompt_for_imagen
from services.blogger_post_cache import BloggerPostDraft
from services.gemini_image_client import GeminiImageResult

if TYPE_CHECKING:
    from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)

# На /api/v1/images доступен flux.2-pro (input_references 0–8).
# Slug'и flux-1.1-pro / flux-dev в Images API OpenRouter отсутствуют.
OPENROUTER_COVER_MODEL_ID = "black-forest-labs/flux.2-pro"
OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"
BLOGGER_COVER_ASPECT_RATIO = "16:9"
OPENROUTER_COVER_TIMEOUT_SEC = 180.0

_FACE_PROMPT_SUFFIX = (
    ", seamlessly integrating the face and appearance of the person from the "
    "reference image into the scene as the main character, matching natural "
    "lighting and skin texture"
)
_OBJECT_PROMPT_SUFFIX = (
    ", seamlessly integrating the specific product/object from the reference "
    "image into the scene, matching local shadows, reflections, and ambient light"
)


class CoverIntegrationType(str, Enum):
    """Режим интеграции референса в обложку."""

    NONE = "none"
    FACE = "face"
    OBJECT = "object"


class BloggerCoverOutcome(str, Enum):
    PROMPT_NOT_FOUND = "prompt_not_found"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    FREE_IMAGE_MODEL_BLOCKED = "free_image_model_blocked"
    OPENROUTER_UNAVAILABLE = "openrouter_unavailable"
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
    """Черновик поста из in-memory кэша."""
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
    """Английский промпт для Flux — только regex/sanitize, без LLM."""
    cleaned = sanitize_blogger_image_prompt_for_imagen(raw_prompt)
    cleaned = re.sub(r"\s*--ar\s*\d+:\d+\s*", " ", cleaned or "", flags=re.IGNORECASE).strip()
    if with_face:
        low = cleaned.lower()
        if "person" not in low and "portrait" not in low and "face" not in low:
            cleaned = (
                "High-end editorial portrait of a person as the clear central subject, "
                + cleaned
            )
    return cleaned


def parse_cover_generate(data: str) -> tuple[str, str] | None:
    """Из ``cover_generate:<none|face|object>:<post_id>``."""
    from content import messages as msg

    prefix = msg.CB_COVER_GENERATE_PREFIX
    raw = (data or "").strip()
    if not raw.startswith(prefix):
        return None
    rest = raw[len(prefix) :]
    if ":" not in rest:
        return None
    mode, post_id = rest.split(":", 1)
    mode = mode.strip().lower()
    post_id = post_id.strip()
    if mode not in msg.COVER_GENERATE_MODES or not post_id:
        return None
    return mode, post_id


def openrouter_cover_configured(settings: Settings) -> bool:
    """True, если задан ``OPENROUTER_API_KEY``."""
    return bool((settings.openrouter_key or "").strip())


async def _get_telegram_file_url(
    file_id: str,
    *,
    bot: Any,
    settings: Settings,
) -> str:
    """``file_id`` → временный прямой URL для OpenRouter ``input_references``.

    Формат Bot API (не ``telegram.org{token}/...`` — такой URL нерабочий)::

        https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}

    Ссылка живёт ~1 час — для генерации Flux достаточно.
    """
    fid = (file_id or "").strip()
    if not fid:
        raise RuntimeError("Telegram file_id is empty")

    token = (settings.tg_token or "").strip()
    if not token:
        token = str(getattr(bot, "token", "") or "").strip()
    if not token:
        raise RuntimeError("TG_TOKEN is not set")

    file_info = await bot.get_file(fid)
    file_path = getattr(file_info, "file_path", None) or ""
    if not file_path:
        raise RuntimeError("Telegram did not return file_path for photo")

    return f"https://api.telegram.org/file/bot{token}/{file_path}"


async def get_public_url_from_telegram(
    bot: Any,
    file_id: str,
    bot_token: str,
) -> str:
    """Публичный алиас: ``file_id`` → ``api.telegram.org/file/bot...``."""

    class _TokenSettings:
        tg_token = bot_token

    return await _get_telegram_file_url(file_id, bot=bot, settings=_TokenSettings())  # type: ignore[arg-type]


def _cover_prompt_for_integration(
    cleaned_prompt: str,
    integration: CoverIntegrationType,
) -> str:
    prompt = (cleaned_prompt or "").strip()
    if integration is CoverIntegrationType.FACE:
        return f"{prompt}{_FACE_PROMPT_SUFFIX}" if prompt else _FACE_PROMPT_SUFFIX.lstrip(", ")
    if integration is CoverIntegrationType.OBJECT:
        return f"{prompt}{_OBJECT_PROMPT_SUFFIX}" if prompt else _OBJECT_PROMPT_SUFFIX.lstrip(", ")
    return prompt


def _parse_openrouter_image_payload(payload: dict[str, Any]) -> GeminiImageResult:
    """``data[0].url`` или ``data[0].b64_json`` → ``GeminiImageResult``."""
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("OpenRouter images: empty data[]")
    item = data[0]
    if not isinstance(item, dict):
        raise RuntimeError("OpenRouter images: data[0] is not an object")

    final_url = item.get("url")
    if isinstance(final_url, str) and final_url.strip():
        return GeminiImageResult(url=final_url.strip())

    b64_raw = item.get("b64_json")
    if isinstance(b64_raw, str) and b64_raw.strip():
        raw = b64_raw.strip()
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise RuntimeError("OpenRouter images: invalid b64_json") from exc
        if not image_bytes:
            raise RuntimeError("OpenRouter images: empty b64_json")
        return GeminiImageResult(data=image_bytes)

    raise RuntimeError("OpenRouter images: neither url nor b64_json in data[0]")


async def generate_blogger_cover_image(
    settings: Settings,
    cleaned_prompt: str,
    *,
    integration: CoverIntegrationType = CoverIntegrationType.NONE,
    photo_file_id: str | None = None,
    source_file_url: str | None = None,
    bot: Any | None = None,
) -> GeminiImageResult:
    """Генерация обложки через OpenRouter Images API (Flux.2 Pro, 16:9).

    Любая сетевая/парсинг-ошибка пробрасывается наверх — ``run_blogger_cover_turn``
    обязан вызвать ``refund_charge`` и вернуть 3 кристалла.
    """
    from services.openrouter_http import get_openrouter_http_client

    api_key = (settings.openrouter_key or "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    prompt = _cover_prompt_for_integration(cleaned_prompt, integration)
    if not prompt:
        raise RuntimeError("OpenRouter images: empty prompt")

    body: dict[str, Any] = {
        "model": OPENROUTER_COVER_MODEL_ID,
        "aspect_ratio": BLOGGER_COVER_ASPECT_RATIO,
        "prompt": prompt,
    }

    ref_url = (source_file_url or "").strip() or None
    if (
        not ref_url
        and photo_file_id
        and bot is not None
        and integration is not CoverIntegrationType.NONE
    ):
        ref_url = await _get_telegram_file_url(
            photo_file_id,
            bot=bot,
            settings=settings,
        )

    if ref_url and integration is not CoverIntegrationType.NONE:
        body["input_references"] = [ref_url]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        client = await get_openrouter_http_client(settings)
        async with asyncio.timeout(OPENROUTER_COVER_TIMEOUT_SEC):
            response = await client.post(
                OPENROUTER_IMAGES_URL,
                headers=headers,
                json=body,
                timeout=httpx.Timeout(OPENROUTER_COVER_TIMEOUT_SEC, connect=30.0),
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenRouter images HTTP {response.status_code}: "
                f"{(response.text or '')[:200]}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("OpenRouter images: response is not a JSON object")
        result = _parse_openrouter_image_payload(payload)
    except Exception:
        logger.exception(
            "blogger cover OpenRouter failed model=%s integration=%s",
            OPENROUTER_COVER_MODEL_ID,
            integration.value,
        )
        raise

    logger.info(
        "blogger cover ok model=%s integration=%s has_ref=%s",
        OPENROUTER_COVER_MODEL_ID,
        integration.value,
        bool(ref_url and integration is not CoverIntegrationType.NONE),
    )
    return result


async def run_product_cover_generation(
    settings: Settings,
    message: Message,
    *,
    photo_file_id: str,
    post_id: str | None,
) -> BloggerCoverResult:
    """Пайплайн «обложка с продуктом»: сохранить file_id → биллинг → OpenRouter."""
    from services.billing.blogger_pipeline import can_afford_blogger_cover
    from services.god_mode import billing_bypass
    from services.repository import set_blogger_object_file_id

    if message.from_user is None:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.GENERATION_FAILED)

    user_id = message.from_user.id
    file_id = (photo_file_id or "").strip()
    if not file_id:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.GENERATION_FAILED)

    await set_blogger_object_file_id(user_id, file_id)

    pid = (post_id or "").strip()
    draft: BloggerPostDraft | None = None
    if pid:
        draft = blogger_post_cache.get(pid, user_id)
        if draft is None:
            draft = await blogger_post_cache.resolve(pid, user_id)
    if draft is None:
        draft = await blogger_post_cache.resolve_last(user_id)

    if draft is None:
        from content import messages as msg
        from aiogram.enums import ParseMode

        await message.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, parse_mode=ParseMode.HTML)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        from content import messages as msg
        from aiogram.enums import ParseMode

        await message.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, parse_mode=ParseMode.HTML)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)

    result = await run_blogger_cover_turn(
        settings,
        user_id=user_id,
        draft=draft,
        use_object=True,
        bot=message.bot,
        photo_file_id=file_id,
    )
    await deliver_blogger_cover_turn_result(message, result, draft=draft)
    return result


async def run_blogger_cover_turn(
    settings: Settings,
    *,
    user_id: int,
    draft: BloggerPostDraft,
    use_face: bool = False,
    use_object: bool = False,
    bot: Any | None = None,
    photo_file_id: str | None = None,
    source_file_url: str | None = None,
) -> BloggerCoverResult:
    """Промпт → spend → OpenRouter → success; при ошибке ``refund_charge``."""
    from services.billing import refund_charge
    from services.billing.blogger_pipeline import (
        can_afford_blogger_cover,
        spend_blogger_cover,
    )
    from services.god_mode import billing_bypass
    from services.repository import get_blogger_face_file_id, get_blogger_object_file_id

    raw_prompt = extract_image_prompt_from_draft(draft)
    if not raw_prompt:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    if not openrouter_cover_configured(settings):
        logger.error("blogger cover: OPENROUTER_API_KEY missing uid=%s", user_id)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.OPENROUTER_UNAVAILABLE)

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)

    integration = CoverIntegrationType.NONE
    resolved_file_id = (photo_file_id or "").strip() or None
    if use_face:
        integration = CoverIntegrationType.FACE
        if not resolved_file_id:
            resolved_file_id = (await get_blogger_face_file_id(user_id) or "").strip() or None
    elif use_object:
        integration = CoverIntegrationType.OBJECT
        if not resolved_file_id:
            resolved_file_id = (await get_blogger_object_file_id(user_id) or "").strip() or None

    cleaned_prompt = prepare_blogger_flux_prompt(
        raw_prompt,
        with_face=integration is CoverIntegrationType.FACE,
    )

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
            integration=integration,
            photo_file_id=resolved_file_id,
            source_file_url=source_file_url,
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
        logger.exception(
            "blogger cover generation failed uid=%s post_id=%s",
            user_id,
            draft.post_id,
        )
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
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import BufferedInputFile

    from services.streaming_download import stream_download_to_bytes

    safe_prompt = cleaned_prompt.replace("<", "&lt;").replace(">", "&gt;")
    caption = caption_template.format(prompt=safe_prompt)

    async def _send_bytes(data: bytes) -> None:
        await message.answer_photo(
            photo=BufferedInputFile(data, filename="blogger_cover.webp"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

    if image.data:
        await _send_bytes(image.data)
        return

    if image.url:
        try:
            await message.answer_photo(
                photo=image.url,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return
        except TelegramBadRequest:
            logger.warning(
                "blogger cover: telegram rejected photo url, downloading bytes",
                exc_info=True,
            )
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            data = await stream_download_to_bytes(
                client,
                image.url,
                source="blogger_cover",
                max_bytes=8 * 1024 * 1024,
            )
        if data:
            await _send_bytes(data)
            return

    raise RuntimeError("deliver_blogger_cover_photo: no image data")


async def deliver_blogger_cover_turn_result(
    message: Message,
    result: BloggerCoverResult,
    *,
    draft: BloggerPostDraft,
) -> None:
    """Показывает итог генерации обложки."""
    from content import messages as msg
    from aiogram.enums import ParseMode

    if result.outcome is BloggerCoverOutcome.DAILY_LIMIT_EXCEEDED:
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
        await message.answer(msg.TXT_BLOGGER_COVER_FAILED, parse_mode=ParseMode.HTML)
        return

    if result.outcome is BloggerCoverOutcome.OPENROUTER_UNAVAILABLE:
        await message.answer(msg.TXT_BLOGGER_COVER_OPENROUTER_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    if result.outcome is BloggerCoverOutcome.PROMPT_NOT_FOUND:
        await message.answer(msg.TXT_BLOGGER_IMAGE_PROMPT_NOT_FOUND, parse_mode=ParseMode.HTML)
        return

    if result.outcome in (
        BloggerCoverOutcome.INSUFFICIENT_BALANCE,
        BloggerCoverOutcome.FREE_IMAGE_MODEL_BLOCKED,
    ):
        await message.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, parse_mode=ParseMode.HTML)


async def handle_blogger_cover_callback(
    settings: Settings,
    callback: CallbackQuery,
    draft: BloggerPostDraft,
    *,
    use_face: bool = False,
    use_object: bool = False,
) -> BloggerCoverResult:
    """UX-обёртка для callback-кнопки обложки."""
    from content import messages as msg
    from services.billing.blogger_pipeline import can_afford_blogger_cover
    from services.god_mode import billing_bypass

    if callback.message is None:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    raw_prompt = extract_image_prompt_from_draft(draft)
    if not raw_prompt:
        await callback.answer(msg.TXT_BLOGGER_IMAGE_PROMPT_NOT_FOUND, show_alert=True)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    user_id = callback.from_user.id if callback.from_user else draft.user_id

    if not openrouter_cover_configured(settings):
        await callback.answer(msg.TXT_BLOGGER_COVER_OPENROUTER_UNAVAILABLE, show_alert=True)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.OPENROUTER_UNAVAILABLE)

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        await callback.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, show_alert=True)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)

    await callback.answer(msg.TXT_BLOGGER_COVER_GENERATING)

    result = await run_blogger_cover_turn(
        settings,
        user_id=user_id,
        draft=draft,
        use_face=use_face,
        use_object=use_object,
        bot=callback.message.bot,
    )

    if callback.message is not None:
        await deliver_blogger_cover_turn_result(callback.message, result, draft=draft)

    return result
