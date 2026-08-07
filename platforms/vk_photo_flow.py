"""VK-поток генерации изображений (i2i через Core-роутер)."""

from __future__ import annotations

import logging
from typing import Any

from config import settings
from content import messages as msg
from core.inbound_router import PhotoGenerationRequest, PhotoReference, route_photo_generation
from platforms.vk_adapter import normalize_vk_message
from platforms.vk_messages import vk_answer
from services.billing.image_pipeline import FREE_PHOTO_MODEL_KEY, free_tier_image_model
from services.generation_jobs import fire_photo_job
from services.use_cases.photo_generation_turn import PhotoGenOutcome
from services.photo_edit_session import get_photo_edit_session
from services.vk_refine_pending import clear_vk_refine_pending, peek_vk_refine_pending
from services.vk_reference_store import (
    CachedVkPhotoRef,
    _peer_lock,
    cache_vk_photo_from_url,
    clear_pending_vk_photo,
    take_pending_vk_photo,
)

logger = logging.getLogger(__name__)

# peer_id → (model_id, label, aspect_ratio). Стейт изолирован по peer_id.
_vk_image_model: dict[int, tuple[str, str, str]] = {}


def enter_vk_image_mode(peer_id: int) -> None:
    model_id = free_tier_image_model() or FREE_PHOTO_MODEL_KEY
    _vk_image_model[peer_id] = (model_id, "Flux FREE", "1:1")


async def activate_vk_photo_refine(*, peer_id: int, user_id: int, message: Any | None = None) -> bool:
    """
    Включает режим доработки для peer_id (после callback-кнопки или текста).

    Returns:
        True если сессия найдена и режим активирован.
    """
    from services.vk_refine_pending import mark_vk_refine_pending

    if peer_id not in _vk_image_model:
        enter_vk_image_mode(peer_id)

    session = get_photo_edit_session(user_id, peer_id=peer_id)
    if session is None:
        if message is not None:
            await vk_answer(message, msg.TXT_PHOTO_REFINE_EXPIRED)
        return False

    if session.image_model_id:
        _vk_image_model[peer_id] = (
            session.image_model_id,
            session.image_model_label or "модель",
            session.aspect_ratio,
        )

    mark_vk_refine_pending(peer_id, user_id)
    if message is not None:
        await vk_answer(message, msg.TXT_PHOTO_REFINE_PROMPT)
    return True


async def handle_vk_photo_refine_event(*, peer_id: int, user_id: int, bot: Any) -> None:
    """Обработка MESSAGE_EVENT (inline callback «✏️ Доработать»)."""
    from platforms.vk_messages import vk_send_message

    ok = await activate_vk_photo_refine(peer_id=peer_id, user_id=user_id)
    if ok:
        await vk_send_message(bot, peer_id, msg.TXT_PHOTO_REFINE_PROMPT)
    else:
        await vk_send_message(bot, peer_id, msg.TXT_PHOTO_REFINE_EXPIRED)


def clear_vk_image_mode(peer_id: int) -> None:
    clear_pending_vk_photo(peer_id)
    clear_vk_refine_pending(peer_id)
    _vk_image_model.pop(peer_id, None)


def set_vk_image_aspect_ratio(peer_id: int, aspect_ratio: str) -> None:
    from services.photo_aspect_ratio import normalize_photo_aspect_ratio

    entry = _vk_image_model.get(peer_id)
    if entry is None:
        return
    model_id, label, _ = entry
    _vk_image_model[peer_id] = (model_id, label, normalize_photo_aspect_ratio(aspect_ratio))


async def handle_vk_photo_message(message: Any) -> bool:
    """
    Обрабатывает сообщение VK в режиме генерации изображений.

    Returns:
        True если сообщение обработано (режим /image активен).
    """
    inbound = normalize_vk_message(message)
    peer_id = inbound.peer_id
    if peer_id not in _vk_image_model:
        return False

    model_id, model_label, aspect_ratio = _vk_image_model[peer_id]
    photo_url = inbound.reference_image_url
    prompt = inbound.text

    refine_triggers = {msg.BTN_PHOTO_REFINE, "✏️ Доработать", "доработать"}
    if (prompt or "").strip() in refine_triggers:
        await activate_vk_photo_refine(
            peer_id=peer_id,
            user_id=inbound.user_id,
            message=message,
        )
        return True

    edit_session = get_photo_edit_session(inbound.user_id, peer_id=peer_id)
    refine_user = peek_vk_refine_pending(peer_id)
    use_edit_session = refine_user == inbound.user_id and edit_session is not None

    async with _peer_lock(peer_id):
        if photo_url and not prompt:
            cached = await cache_vk_photo_from_url(peer_id, photo_url)
            if cached is None:
                await vk_answer(message, msg.TXT_GEN_JOB_FAILED)
                return True
            await vk_answer(message, msg.TXT_CREATE_IMAGE_WAIT_PROMPT)
            return True

        pending = None
        if not photo_url and prompt:
            pending = take_pending_vk_photo(peer_id)

        ref: CachedVkPhotoRef | None = None
        if photo_url:
            ref = await cache_vk_photo_from_url(peer_id, photo_url)
            if ref is None:
                await vk_answer(message, msg.TXT_GEN_JOB_FAILED)
                return True
        elif pending is not None:
            ref = pending
        elif use_edit_session and prompt and edit_session is not None:
            model_id = edit_session.image_model_id or model_id
            model_label = edit_session.image_model_label or model_label
            aspect_ratio = edit_session.aspect_ratio or aspect_ratio

        if not prompt and ref is None:
            await vk_answer(message, msg.TXT_CREATE_IMAGE_AFTER_MODEL)
            return True

        photo_ref: PhotoReference | None = None
        if ref is not None:
            photo_ref = PhotoReference(
                reference_image_bytes=ref.data,
                reference_mime=ref.mime,
            )
        elif (
            use_edit_session
            and edit_session is not None
            and edit_session.reference_image_bytes
            and prompt
        ):
            photo_ref = PhotoReference(
                reference_image_bytes=edit_session.reference_image_bytes,
                reference_mime=edit_session.reference_mime,
            )

        if use_edit_session and prompt:
            from services.photo_edit_session import update_photo_edit_session_aspect_ratio
            from services.photo_intent_parser import resolve_photo_edit_prompt

            aspect_ratio, prompt, aspect_changed = await resolve_photo_edit_prompt(
                prompt,
                current_aspect=aspect_ratio,
            )
            if aspect_changed:
                set_vk_image_aspect_ratio(peer_id, aspect_ratio)
                update_photo_edit_session_aspect_ratio(inbound.user_id, aspect_ratio)

        req = PhotoGenerationRequest(
            platform="vk",
            user_id=inbound.user_id,
            chat_id=peer_id,
            prompt=prompt or "Улучши это фото",
            image_model_id=model_id,
            image_model_label=model_label,
            photo_ref=photo_ref,
            aspect_ratio=aspect_ratio,
        )

        await vk_answer(message, msg.TXT_GEN_STATUS_ACCEPTED)

        try:
            result = await route_photo_generation(settings, req, bot=None)
        except Exception:
            logger.exception("vk photo: billing/enqueue failed peer_id=%s uid=%s", peer_id, inbound.user_id)
            await vk_answer(message, msg.TXT_GEN_JOB_FAILED)
            return True

        if result.outcome is PhotoGenOutcome.NEED_PROMPT:
            await vk_answer(message, msg.TXT_CREATE_IMAGE_AFTER_MODEL)
            return True
        if result.outcome is PhotoGenOutcome.INSUFFICIENT_BALANCE:
            await vk_answer(message, msg.TXT_INSUFFICIENT_BALANCE)
            return True
        if result.outcome is PhotoGenOutcome.DAILY_LIMIT_EXCEEDED:
            await vk_answer(
                message,
                msg.TXT_PHOTO_DAILY_LIMIT.format(limit=settings.free_daily_photo_limit),
            )
            return True
        if result.outcome is PhotoGenOutcome.GLOBAL_FREE_IMAGE_CAP:
            await vk_answer(message, msg.TXT_FREE_IMAGE_GLOBAL_CAP)
            return True
        if result.outcome is PhotoGenOutcome.FREE_IMAGE_MODEL_BLOCKED:
            await vk_answer(message, msg.TXT_FREE_IMAGE_MODEL_BLOCKED)
            return True

        eq = result.enqueue
        if eq is None:
            await vk_answer(message, msg.TXT_GEN_JOB_FAILED)
            return True

        fire_photo_job(
            None,
            peer_id,
            inbound.user_id,
            eq.image_model_id,
            eq.model_label,
            eq.prompt,
            eq.used_daily_slot,
            eq.charged_crystals,
            priority=eq.priority,
            billing_charge_id=eq.billing_charge_id,
            reference_image_bytes=eq.reference_image_bytes,
            reference_mime=eq.reference_mime,
            aspect_ratio=eq.aspect_ratio,
            platform="vk",
        )
        if result.vip_priority:
            await vk_answer(message, msg.TXT_GEN_STATUS_VIP)
        clear_vk_refine_pending(peer_id)
        return True
