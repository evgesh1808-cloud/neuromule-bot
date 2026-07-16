"""AI-обложки блогера: OpenRouter Images API + async-очередь (highload).

Replicate не используется. FACE/OBJECT-референсы уходят как base64 data-URL
(OpenRouter не скачивает ``api.telegram.org/file/bot...``).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import TYPE_CHECKING, Any

import httpx
from aiogram.types import Message

from config import Settings
from services import blogger_post_cache
from services.blogger_image_prompt import sanitize_blogger_image_prompt_for_imagen
from services.blogger_post_cache import BloggerPostDraft
from services.gemini_image_client import GeminiImageResult

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)

# Актуальный флагман BFL на OpenRouter Images (input_references 0–8).
OPENROUTER_COVER_MODEL_ID = "black-forest-labs/flux.2-pro"
OPENROUTER_IMAGES_URL = "https://openrouter.ai/api/v1/images"
BLOGGER_COVER_ASPECT_RATIO = "16:9"
OPENROUTER_COVER_TIMEOUT_SEC = 180.0
MAX_COVER_REFERENCE_BYTES = 8 * 1024 * 1024
COVER_WORKER_RATE_LIMIT_SEC = 2.0
COVER_TYPING_INTERVAL_SEC = 4.0

# Общая очередь: пул воркеров безопасно разбирает её через await Queue.get().
cover_generation_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
_worker_tasks: list[asyncio.Task[None]] = []
DEFAULT_COVER_WORKERS = 5

# FACE: макро-хвост под фотореализм кожи (Flux + face reference).
_FACE_PROMPT_SUFFIX = (
    ", close-up portrait, high-end editorial fashion photography, masterpiece, "
    "hyper-realistic detailed skin texture, visible skin pores, natural skin grain, "
    "subtle realistic skin details, professional volumetric studio lighting, "
    "rich contrast, shot on 85mm lens, f/1.4 aperture, cinematic depth of field, "
    "sharp focus on eyes and face, highly commercial lifestyle aesthetic, "
    "absolutely no plastic skin or artificial smoothing filters"
)
# OBJECT / NONE: общий коммерческий фотореализм.
_COMMERCIAL_PROMPT_SUFFIX = (
    ", ultra-detailed, 8k resolution, photorealistic, high-end studio lighting, "
    "sharp focus, masterpiece, highly commercial aesthetic"
)


class CoverIntegrationType(str, Enum):
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
    QUEUED = "queued"
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
    from services.blogger_post_parser import extract_blogger_image_prompt

    return extract_blogger_image_prompt(draft.raw_text, draft.parsed)


def prepare_blogger_flux_prompt(raw_prompt: str, *, with_face: bool = False) -> str:
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
    return bool((settings.openrouter_key or "").strip())


def _mime_from_telegram_path(file_path: str | None) -> str:
    path = (file_path or "").lower()
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _cover_prompt_for_integration(
    cleaned_prompt: str,
    integration: CoverIntegrationType,
) -> str:
    """Склеивает cleaned_prompt + хвостик качества перед JSON-payload в OpenRouter."""
    prompt = (cleaned_prompt or "").strip()
    if integration is CoverIntegrationType.FACE:
        suffix = _FACE_PROMPT_SUFFIX
    else:
        # OBJECT и NONE — один коммерческий хвостик
        suffix = _COMMERCIAL_PROMPT_SUFFIX
    if not prompt:
        return suffix.lstrip(", ")
    return f"{prompt}{suffix}"


def _openrouter_input_reference(data_url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": data_url}}


async def _telegram_file_id_to_data_url(bot: Any, file_id: str) -> str:
    """Bot API download → ``data:image/...;base64,...`` для OpenRouter."""
    fid = (file_id or "").strip()
    if not fid:
        raise RuntimeError("Telegram file_id is empty")

    file_info = await bot.get_file(fid)
    file_path = getattr(file_info, "file_path", None) or ""
    if not file_path:
        raise RuntimeError("Telegram did not return file_path for photo")

    buffer = BytesIO()
    await bot.download_file(file_path, destination=buffer)
    raw = buffer.getvalue()
    if not raw:
        raise RuntimeError("empty photo payload from Telegram")
    if len(raw) > MAX_COVER_REFERENCE_BYTES:
        raise RuntimeError(
            f"cover reference too big: {len(raw)} bytes (max {MAX_COVER_REFERENCE_BYTES})"
        )

    mime = _mime_from_telegram_path(file_path)
    encoded = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _parse_openrouter_image_payload(payload: dict[str, Any]) -> GeminiImageResult:
    """``data[0].url`` / ``data[0].b64_json`` (и плоский ``data.url``) → результат."""
    data = payload.get("data")
    if isinstance(data, dict):
        item = data
    elif isinstance(data, list) and data:
        item = data[0]
        if not isinstance(item, dict):
            raise RuntimeError("OpenRouter images: data[0] is not an object")
    else:
        raise RuntimeError("OpenRouter images: empty data")

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

    raise RuntimeError("OpenRouter images: neither url nor b64_json")


async def generate_blogger_cover_image(
    settings: Settings,
    cleaned_prompt: str,
    *,
    integration: CoverIntegrationType = CoverIntegrationType.NONE,
    photo_file_id: str | None = None,
    source_base64_url: str | None = None,
    bot: Any | None = None,
) -> GeminiImageResult:
    """Синхронный вызов OpenRouter Images (используется воркером очереди)."""
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

    ref = (source_base64_url or "").strip() or None
    if (
        not ref
        and photo_file_id
        and bot is not None
        and integration is not CoverIntegrationType.NONE
    ):
        ref = await _telegram_file_id_to_data_url(bot, photo_file_id)

    if integration is not CoverIntegrationType.NONE:
        if not ref:
            raise RuntimeError(
                f"blogger cover {integration.value}: reference photo is required"
            )
        # ContentPartImage: data-URL внутри image_url.url (строка в массиве тоже
        # принимается частью провайдеров, объектный формат — канон OpenRouter).
        body["input_references"] = [_openrouter_input_reference(ref)]

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
        return _parse_openrouter_image_payload(payload)
    except Exception:
        logger.exception(
            "blogger cover OpenRouter failed model=%s integration=%s",
            OPENROUTER_COVER_MODEL_ID,
            integration.value,
        )
        raise


async def _safe_send_text(bot: Any, chat_id: int, text: str, *, context: str) -> None:
    from platforms.telegram_notify import safe_send_user_message

    await safe_send_user_message(
        bot,
        chat_id,
        text,
        context=context,
        parse_mode="HTML",
    )


async def _safe_delete_status_message(
    bot: Any,
    chat_id: int,
    status_message_id: int | None,
) -> None:
    """Удаляет статус «⏳ Запрос принят…»; молчит, если юзер уже снёс сообщение."""
    if status_message_id is None:
        return
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNetworkError,
        TelegramRetryAfter,
    )

    try:
        await bot.delete_message(chat_id=chat_id, message_id=int(status_message_id))
    except (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNetworkError,
        TelegramRetryAfter,
    ):
        # message to delete not found / blocked / transient — штатно
        pass
    except Exception:
        logger.error(
            "blogger cover: unexpected delete_message chat_id=%s message_id=%s",
            chat_id,
            status_message_id,
            exc_info=True,
        )


async def _keep_typing_loop(
    bot: Any,
    chat_id: int,
    stop_typing: asyncio.Event,
) -> None:
    """Пока Flux крутится — каждые 4 с шлём ChatAction.TYPING (Telegram гасит ~5 с)."""
    from aiogram.enums import ChatAction
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNetworkError,
        TelegramRetryAfter,
    )

    while not stop_typing.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramNetworkError,
            TelegramRetryAfter,
        ):
            pass
        except Exception:
            logger.error(
                "blogger cover: unexpected send_chat_action chat_id=%s",
                chat_id,
                exc_info=True,
            )
        try:
            await asyncio.wait_for(stop_typing.wait(), timeout=COVER_TYPING_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue


async def _safe_send_cover_photo(
    bot: Any,
    chat_id: int,
    *,
    cleaned_prompt: str,
    image: GeminiImageResult,
) -> None:
    """Доставка готовой обложки из фонового воркера."""
    from aiogram.enums import ParseMode
    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNetworkError,
        TelegramRetryAfter,
    )
    from aiogram.types import BufferedInputFile

    from config import settings
    from services.streaming_download import stream_download_to_bytes

    # Premium expandable quote (Telegram HTML). Промпт экранируем внутри <code>.
    bot_username = (settings.telegram_bot_username or "NeuroMule_bot").lstrip("@")
    safe_prompt = (cleaned_prompt or "").replace("<", "&lt;").replace(">", "&gt;")
    caption = (
        "🎨 <b>Ваша AI-обложка готова!</b>\n\n"
        f'<blockquote expandable><b><a href="https://t.me/{bot_username}">NeuroMule</a></b>\n'
        f"<code>{safe_prompt}</code></blockquote>"
    )

    async def _send_bytes(data: bytes) -> None:
        await bot.send_photo(
            chat_id,
            photo=BufferedInputFile(data, filename="blogger_cover.webp"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

    try:
        if image.data:
            await _send_bytes(image.data)
            return
        if image.url:
            try:
                await bot.send_photo(
                    chat_id,
                    photo=image.url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
                return
            except TelegramBadRequest:
                logger.warning(
                    "blogger cover worker: telegram rejected photo url, downloading",
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
        raise RuntimeError("no image data to deliver")
    except TelegramForbiddenError:
        logger.info("blogger cover worker: user blocked bot chat_id=%s", chat_id)
    except TelegramRetryAfter as exc:
        logger.warning(
            "blogger cover worker: flood chat_id=%s retry_after=%s",
            chat_id,
            getattr(exc, "retry_after", None),
        )
    except TelegramBadRequest as exc:
        # Невалидный HTML caption / peer — пробрасываем, чтобы воркер сделал refund.
        logger.warning("blogger cover worker: bad request chat_id=%s", chat_id, exc_info=True)
        raise RuntimeError("telegram rejected cover photo") from exc
    except TelegramNetworkError:
        logger.warning("blogger cover worker: network chat_id=%s", chat_id, exc_info=True)
        raise
    except Exception:
        logger.error(
            "blogger cover worker: unexpected send failure chat_id=%s",
            chat_id,
            exc_info=True,
        )
        raise


async def _process_cover_task(task: dict[str, Any]) -> None:
    """Биллинг → base64-референс → OpenRouter → photo / refund (+ typing + cleanup)."""
    from content import messages as msg
    from services.billing import refund_charge
    from services.billing.blogger_pipeline import spend_blogger_cover
    from services.god_mode import billing_bypass

    settings: Settings = task["settings"]
    bot = task["bot"]
    user_id: int = int(task["user_id"])
    chat_id: int = int(task["chat_id"])
    post_id: str = str(task.get("post_id") or "")
    cleaned_prompt: str = str(task["cleaned_prompt"])
    integration = CoverIntegrationType(str(task["integration"]))
    photo_file_id = (task.get("photo_file_id") or "").strip() or None
    def _as_msg_id(raw: Any) -> int | None:
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    status_message_id = _as_msg_id(task.get("status_message_id"))
    # Канон: success_msg_id; success_message_id — совместимость со старыми задачами.
    success_msg_id = _as_msg_id(task.get("success_msg_id")) or _as_msg_id(
        task.get("success_message_id")
    )
    instruction_msg_id = _as_msg_id(task.get("instruction_msg_id"))

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _keep_typing_loop(bot, chat_id, stop_typing),
        name=f"cover_typing_{chat_id}",
    )
    charge_id: str | None = None
    try:
        if not billing_bypass(user_id):
            spend = await spend_blogger_cover(user_id)
            if not spend.ok:
                if spend.error == "daily_limit_exceeded":
                    await _safe_send_text(
                        bot, chat_id, msg.TXT_PHOTO_DAILY_LIMIT, context="blogger_cover_limit"
                    )
                else:
                    await _safe_send_text(
                        bot,
                        chat_id,
                        msg.TXT_BLOGGER_COVER_INSUFFICIENT,
                        context="blogger_cover_balance",
                    )
                return
            charge_id = spend.charge.charge_id if spend.charge else None

        source_base64_url: str | None = None
        if photo_file_id and integration is not CoverIntegrationType.NONE:
            source_base64_url = await _telegram_file_id_to_data_url(bot, photo_file_id)

        image = await generate_blogger_cover_image(
            settings,
            cleaned_prompt,
            integration=integration,
            photo_file_id=photo_file_id,
            source_base64_url=source_base64_url,
            bot=bot,
        )
        if not image.has_image():
            raise RuntimeError("empty image result")

        await _safe_send_cover_photo(
            bot,
            chat_id,
            cleaned_prompt=cleaned_prompt,
            image=image,
        )
        logger.info(
            "blogger cover worker ok uid=%s post_id=%s integration=%s",
            user_id,
            post_id,
            integration.value,
        )
    except Exception:
        if charge_id:
            await refund_charge(charge_id)
        logger.exception(
            "blogger cover worker failed uid=%s post_id=%s",
            user_id,
            post_id,
        )
        await _safe_send_text(
            bot,
            chat_id,
            msg.TXT_BLOGGER_COVER_FAILED,
            context="blogger_cover_failed",
        )
    finally:
        # Останавливаем анимацию «бот печатает…»
        stop_typing.set()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

        # Зачистка временных сообщений (инструкция / ✅ сохранено / ⏳ принято)
        for msg_id in (status_message_id, instruction_msg_id, success_msg_id):
            await _safe_delete_status_message(bot, chat_id, msg_id)


async def cover_queue_worker() -> None:
    """Бесконечный воркер: берёт задачи из общей очереди + пауза под rate-limit."""
    task_name = asyncio.current_task().get_name() if asyncio.current_task() else "cover_worker"
    logger.info("blogger cover queue worker started name=%s", task_name)
    while True:
        task = await cover_generation_queue.get()
        # _process_cover_task: _keep_typing_loop + delete status_message_id в finally
        try:
            await _process_cover_task(task)
        except Exception:
            logger.exception("blogger cover worker: unhandled task error")
        finally:
            cover_generation_queue.task_done()
            await asyncio.sleep(COVER_WORKER_RATE_LIMIT_SEC)


async def start_cover_queue_worker() -> None:
    """Пул параллельных воркеров (вызывать из ``run_telegram``, не на import)."""
    global _worker_tasks
    alive = [t for t in _worker_tasks if not t.done()]
    if alive:
        _worker_tasks = alive
        return

    from config import settings

    num_workers = int(
        getattr(settings, "blogger_cover_workers_count", DEFAULT_COVER_WORKERS) or DEFAULT_COVER_WORKERS
    )
    num_workers = max(1, min(num_workers, 32))
    _worker_tasks = [
        asyncio.create_task(cover_queue_worker(), name=f"cover_worker_{i}")
        for i in range(num_workers)
    ]
    logger.info("blogger cover worker pool started n=%s", num_workers)


async def stop_cover_queue_worker_for_tests() -> None:
    """Только тесты: останавливает пул воркеров и чистит очередь."""
    global _worker_tasks
    for task in _worker_tasks:
        task.cancel()
    for task in _worker_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    _worker_tasks = []
    while not cover_generation_queue.empty():
        try:
            cover_generation_queue.get_nowait()
            cover_generation_queue.task_done()
        except asyncio.QueueEmpty:
            break


async def run_blogger_cover_turn(
    settings: Settings,
    *,
    user_id: int,
    draft: BloggerPostDraft,
    use_face: bool = False,
    use_object: bool = False,
    bot: Any | None = None,
    chat_id: int | None = None,
    photo_file_id: str | None = None,
    success_msg_id: int | None = None,
    instruction_msg_id: int | None = None,
) -> BloggerCoverResult:
    """Валидация + постановка в очередь (без ожидания OpenRouter)."""
    from services.billing.blogger_pipeline import can_afford_blogger_cover
    from services.god_mode import billing_bypass
    from services.repository import get_blogger_face_file_id, get_blogger_object_file_id

    raw_prompt = extract_image_prompt_from_draft(draft)
    if not raw_prompt:
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    if not openrouter_cover_configured(settings):
        logger.error("blogger cover: OPENROUTER_API_KEY missing uid=%s", user_id)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.OPENROUTER_UNAVAILABLE)

    if bot is None:
        logger.error("blogger cover: bot is required for queue uid=%s", user_id)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.GENERATION_FAILED)

    resolved_chat_id = int(chat_id) if chat_id is not None else user_id

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

    from aiogram.enums import ParseMode

    from content import messages as msg

    await start_cover_queue_worker()
    status_msg = await bot.send_message(
        resolved_chat_id,
        msg.TXT_BLOGGER_COVER_QUEUED,
        parse_mode=ParseMode.HTML,
    )
    await cover_generation_queue.put(
        {
            "settings": settings,
            "bot": bot,
            "user_id": user_id,
            "chat_id": resolved_chat_id,
            "post_id": draft.post_id,
            "cleaned_prompt": cleaned_prompt,
            "integration": integration.value,
            "photo_file_id": resolved_file_id,
            "status_message_id": status_msg.message_id,
            "instruction_msg_id": instruction_msg_id,
            "success_msg_id": success_msg_id,
        }
    )
    return BloggerCoverResult(
        outcome=BloggerCoverOutcome.QUEUED,
        cleaned_prompt=cleaned_prompt,
    )


async def run_product_cover_generation(
    settings: Settings,
    message: Message,
    *,
    photo_file_id: str,
    post_id: str | None,
    instruction_msg_id: int | None = None,
    success_msg_id: int | None = None,
) -> BloggerCoverResult:
    """Пайплайн «обложка с продуктом»: сохранить file_id → очередь."""
    from aiogram.enums import ParseMode
    from content import messages as msg
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
        await _safe_delete_status_message(
            message.bot, message.chat.id, instruction_msg_id
        )
        await message.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, parse_mode=ParseMode.HTML)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.PROMPT_NOT_FOUND)

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        await _safe_delete_status_message(
            message.bot, message.chat.id, instruction_msg_id
        )
        await message.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, parse_mode=ParseMode.HTML)
        return BloggerCoverResult(outcome=BloggerCoverOutcome.INSUFFICIENT_BALANCE)

    result = await run_blogger_cover_turn(
        settings,
        user_id=user_id,
        draft=draft,
        use_object=True,
        bot=message.bot,
        chat_id=message.chat.id,
        photo_file_id=file_id,
        instruction_msg_id=instruction_msg_id,
        success_msg_id=success_msg_id,
    )
    await deliver_blogger_cover_turn_result(message, result, draft=draft)
    return result


async def deliver_blogger_cover_turn_result(
    message: Message,
    result: BloggerCoverResult,
    *,
    draft: BloggerPostDraft,
) -> None:
    """UX сразу после постановки в очередь / ошибок валидации (не ждёт OpenRouter)."""
    from aiogram.enums import ParseMode
    from content import messages as msg

    if result.outcome is BloggerCoverOutcome.QUEUED:
        # Статус «⏳ Ваш запрос принят…» уже отправлен в run_blogger_cover_turn
        # и будет удалён воркером по status_message_id.
        logger.info(
            "blogger cover queued uid=%s post_id=%s",
            draft.user_id,
            draft.post_id,
        )
        return

    if result.outcome is BloggerCoverOutcome.DAILY_LIMIT_EXCEEDED:
        await message.answer(msg.TXT_PHOTO_DAILY_LIMIT, parse_mode=ParseMode.HTML)
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
        return

    if result.outcome is BloggerCoverOutcome.GENERATION_FAILED:
        await message.answer(msg.TXT_BLOGGER_COVER_FAILED, parse_mode=ParseMode.HTML)


async def handle_blogger_cover_callback(
    settings: Settings,
    callback: CallbackQuery,
    draft: BloggerPostDraft,
    *,
    use_face: bool = False,
    use_object: bool = False,
) -> BloggerCoverResult:
    """UX-обёртка: валидация → очередь → мгновенный ответ «принято»."""
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

    await callback.answer()

    result = await run_blogger_cover_turn(
        settings,
        user_id=user_id,
        draft=draft,
        use_face=use_face,
        use_object=use_object,
        bot=callback.message.bot,
        chat_id=callback.message.chat.id,
    )
    await deliver_blogger_cover_turn_result(callback.message, result, draft=draft)
    return result
