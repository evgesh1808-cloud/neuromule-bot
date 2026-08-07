"""SMART_MODE dispatch: billing preview + fire_photo_job (Telegram / VK, FSM=None)."""

from __future__ import annotations

import logging
from typing import Any

from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from content import messages as msg
from services.agent_intent import detect_image_intent
from services.billing import store
from services.billing.daily_quotas import (
    get_free_photo_snapshot,
    get_global_free_image_snapshot,
    quota_day,
)
from services.billing.image_pipeline import build_image_spend_plan, normalize_image_model
from services.billing.pricing import FREE_IMAGEN_DAILY_LIMIT
from services.generation_jobs import fire_photo_job
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn

logger = logging.getLogger(__name__)


def format_agent_image_ack(model_label: str, aspect_ratio: str) -> str:
    return (
        f"🤖 Распознал команду на рисование! "
        f"Запускаю {model_label} в формате {aspect_ratio}..."
    )


async def preview_image_affordability(user_id: int, model_key: str) -> tuple[bool, str | None]:
    """Проверка баланса по pricing_constants / build_image_spend_plan без списания."""
    user = await store.load_user_billing(user_id)
    normalized = normalize_image_model(model_key)
    today = quota_day()
    snap = await get_free_photo_snapshot(user_id, day=today)
    plan = build_image_spend_plan(
        user.current_tariff,
        normalized,
        daily_count=snap.used,
        daily_date=today,
    )

    if plan.blocked:
        return False, msg.TXT_FREE_IMAGE_MODEL_BLOCKED

    if plan.use_free_daily_slot:
        global_snap = await get_global_free_image_snapshot(day=today)
        if global_snap.exhausted:
            return False, msg.TXT_FREE_IMAGE_GLOBAL_CAP
        if snap.used >= FREE_IMAGEN_DAILY_LIMIT:
            return False, msg.TXT_PHOTO_DAILY_LIMIT.format(limit=FREE_IMAGEN_DAILY_LIMIT)
        return True, None

    if plan.crystals_only:
        if user.total_crystals >= plan.crystal_cost:
            return True, None
        return False, msg.TXT_INSUFFICIENT_BALANCE

    if user.total_energy >= plan.energy_cost:
        return True, None
    if user.total_crystals >= plan.crystal_cost:
        return True, None
    return False, msg.TXT_INSUFFICIENT_BALANCE


async def enqueue_image_generation_job(
    *,
    platform: str,
    user_id: int,
    chat_id: int,
    model_id: str,
    model_label: str,
    prompt: str,
    aspect_ratio: str,
    bot: Any | None,
) -> PhotoGenOutcome:
    """Списание + ``fire_photo_job`` (общий пайплайн Smart Mode / Mini App)."""
    intent = {
        "model_id": model_id,
        "model_label": model_label,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
    }
    return await _enqueue_agent_image_job(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        intent=intent,
        bot=bot,
    )


async def notify_webapp_queue_accepted(
    *,
    platform: str,
    chat_id: int,
    bot: Any | None,
) -> None:
    text = msg.TXT_WEBAPP_QUEUE_ACK
    if platform == "telegram" and bot is not None:
        from platforms.telegram_notify import safe_send_user_message

        await safe_send_user_message(
            bot,
            chat_id,
            text,
            context="webapp_queue_ack",
        )
        return
    if platform == "vk":
        from platforms.vk_runtime import get_vk_bot
        from platforms.vk_messages import vk_send_message

        vk_bot = get_vk_bot()
        if vk_bot is not None:
            await vk_send_message(vk_bot, chat_id, text)


async def run_webapp_image_pipeline(
    *,
    platform: str,
    user_id: int,
    chat_id: int,
    model_id: str,
    model_label: str,
    prompt: str,
    aspect_ratio: str,
    bot: Any | None,
) -> tuple[PhotoGenOutcome, str | None]:
    """Preview баланса → enqueue → уведомление в чат."""
    ok, refusal = await preview_image_affordability(user_id, model_id)
    if not ok:
        return PhotoGenOutcome.INSUFFICIENT_BALANCE, refusal

    outcome = await enqueue_image_generation_job(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        model_id=model_id,
        model_label=model_label,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        bot=bot,
    )
    if outcome is PhotoGenOutcome.SUCCESS:
        await notify_webapp_queue_accepted(platform=platform, chat_id=chat_id, bot=bot)
    return outcome, None


async def _enqueue_agent_image_job(
    *,
    platform: str,
    user_id: int,
    chat_id: int,
    intent: dict[str, Any],
    bot: Any | None,
) -> PhotoGenOutcome:
    model_id = str(intent["model_id"])
    model_label = str(intent["model_label"])
    prompt = str(intent["prompt"])
    aspect = str(intent["aspect_ratio"])

    result = await run_photo_generation_turn(
        settings,
        bot,
        chat_id,
        user_id,
        model_id,
        model_label,
        prompt,
        aspect_ratio=aspect,
    )
    if result.outcome is not PhotoGenOutcome.SUCCESS or result.enqueue is None:
        return result.outcome

    eq = result.enqueue
    fire_photo_job(
        bot if platform == "telegram" else None,
        chat_id,
        user_id,
        eq.image_model_id,
        eq.model_label,
        eq.prompt,
        eq.used_daily_slot,
        eq.charged_crystals,
        priority=eq.priority,
        billing_charge_id=eq.billing_charge_id,
        aspect_ratio=eq.aspect_ratio,
        platform=platform,  # type: ignore[arg-type]
    )
    return PhotoGenOutcome.SUCCESS


async def try_agent_image_intent_telegram(message: Message, state: FSMContext) -> bool:
    """Перехват текста в главном меню (FSM=None). Returns True если обработано."""
    if message.from_user is None:
        return False
    if await state.get_state() is not None:
        return False

    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return False

    from services.agent_intent import looks_like_image_generation_request

    if not looks_like_image_generation_request(text):
        return False

    intent = await detect_image_intent(text)
    if intent is None:
        return False

    user_id = message.from_user.id
    ok, refusal = await preview_image_affordability(user_id, intent["model_id"])
    if not ok:
        await message.answer(refusal or msg.TXT_INSUFFICIENT_BALANCE, parse_mode=ParseMode.HTML)
        return True

    await message.answer(
        format_agent_image_ack(intent["model_label"], intent["aspect_ratio"]),
        parse_mode=ParseMode.HTML,
    )

    from platforms.handlers import deps

    outcome = await _enqueue_agent_image_job(
        platform="telegram",
        user_id=user_id,
        chat_id=message.chat.id,
        intent=intent,
        bot=deps.bot(),
    )
    if outcome is not PhotoGenOutcome.SUCCESS:
        await message.answer(msg.TXT_GEN_JOB_FAILED, parse_mode=ParseMode.HTML)
    return True


async def try_agent_image_intent_vk(message: Any) -> bool:
    """Перехват текста VK вне режима /image (peer не в image mode)."""
    from platforms.vk_messages import vk_answer
    from platforms.vk_photo_flow import _vk_image_model

    text = (getattr(message, "text", None) or "").strip()
    uid = int(getattr(message, "from_id", 0) or 0)
    peer_id = int(getattr(message, "peer_id", 0) or 0)
    if not text or not uid or text.startswith("/"):
        return False
    if peer_id in _vk_image_model:
        return False

    from services.agent_intent import looks_like_image_generation_request

    if not looks_like_image_generation_request(text):
        return False

    intent = await detect_image_intent(text)
    if intent is None:
        return False

    ok, refusal = await preview_image_affordability(uid, intent["model_id"])
    if not ok:
        await vk_answer(message, refusal or msg.TXT_INSUFFICIENT_BALANCE)
        return True

    await vk_answer(
        message,
        format_agent_image_ack(intent["model_label"], intent["aspect_ratio"]),
    )

    outcome = await _enqueue_agent_image_job(
        platform="vk",
        user_id=uid,
        chat_id=peer_id,
        intent=intent,
        bot=None,
    )
    if outcome is not PhotoGenOutcome.SUCCESS:
        await vk_answer(message, msg.TXT_GEN_JOB_FAILED)
    return True
