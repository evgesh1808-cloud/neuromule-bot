"""Telegram handlers (FSMContext → RedisStorage при REDIS_URL, см. ``build_fsm_storage``)."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from io import BytesIO
from pathlib import Path

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from config import settings
from content import messages as msg
from content.video_menu import (
    CB_VIDEO_CAT_PREFIX,
    CB_VIDEO_EXTEND,
    CB_VIDEO_LONG,
    CB_VIDEO_PREFIX,
    video_category_menu,
    video_root_menu,
)
from platforms.handlers import deps
from platforms.telegram_keyboards import (
    cabinet_keyboard,
    channel_gate_markup,
    create_menu,
    get_admin_inline_keyboard,
    hd_menu,
    hd_pro_unlocked_keyboard,
    hd_report_sections_markup,
    image_model_menu,
    image_aspect_ratio_menu,
    invite_limit_keyboard,
    main_menu,
    photo_tools_menu,
    service_rules_menu,
    support_faq_keyboard,
    terms_accept_keyboard,
    text_role_menu,
)
from platforms.telegram_states import (
    AdminStates,
    FeedbackStates,
    OneCAuditingStates,
    OzonAuditingStates,
    UserFlow,
    WBAuditingStates,
    YandexAuditingStates,
)
from platforms.telegram_utils import (
    HelpInstructionWordFilter,
    _extract_ticket_user_id,
    _feedback_ticket_header,
    _reply_menu_button_texts,
    _reply_video_gen_result,
    is_admin_user,
    notify_admins_about_payment,
    send_same_as_instruction_button,
    try_dispatch_reply_nav_button,
)
from services import hd_service
from services import payments_catalog as paycat
from services.billing import billing
from services.billing.store import refund_charge
from services.hd_logic import (
    birth_data_minimum_for_advice,
    change_user_crystals,
    create_pdf,
    daily_advice_user_profile_from_repo_user,
    format_premium_report,
    generate_daily_forecast,
    generate_premium_report,
    get_calculated_gates,
    get_dynamic_cta_for_today,
    get_user,
    parse_birth_for_daily_advice,
    parse_hd_request,
    parse_match_request,
    premium_report_from_json,
    premium_report_to_json,
    today_iso,
    try_consume_crystals,
    update_user,
)
from services.repository import (
    add_promo_code,
    clear_user_dialog_and_memory,
    commit_daily_advice,
    ensure_user,
    get_sales_stats,
    get_user_row,
    list_all_user_ids,
    rollback_daily_advice,
    sales_stats_as_dict,
    set_user_accepted_terms,
    try_begin_daily_advice,
    update_balance,
)
from services.telegram_safe_text import sanitize_telegram_plain_text
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.telegram_chunks import answer_chat_text
from platforms.telegram_quote import (
    REPLY_TO_BOT_FILTER,
    build_quoted_user_prompt,
    has_neurotext_message_input,
    resolve_neurotext_quote_input,
)
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn
from services.use_cases.music_generation_turn import MusicGenOutcome, run_music_generation_turn
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.payment_turn import PaymentApplyOutcome, run_successful_payment_apply
from services.generation_jobs import fire_photo_job
from services.photo_aspect_ratio import normalize_photo_aspect_ratio
from services.photo_edit_session import get_photo_edit_session
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn
from services.use_cases.promo_turn import PromoOutcome, run_promo_redeem
from services.use_cases.start_turn import StartFlowOutcome, run_start_turn
from services.use_cases.tariff_shop_nav_turn import TariffShopNavOutcome, resolve_tariff_shop_callback
from services.use_cases.video_generation_turn import (
    VideoGenOutcome,
    VideoGenResult,
    classify_scenario_pick,
    run_video_scenario_turn,
)

logger = logging.getLogger(__name__)

router = Router()


async def _dispatch_nav_or_none(message: Message, state: FSMContext) -> bool:
    """True если Reply nav-кнопка обработана (вызывающий handler должен return)."""
    return await try_dispatch_reply_nav_button(message, state)

# Один активный photo/chat turn на user_id — защита от двойного списания при retry Telegram.
user_locks: dict[int, asyncio.Lock] = {}


class PendingImageMenuTextFilter(BaseFilter):
    """Текст после меню фото без выбора inline-модели — не пускать в чат."""

    async def __call__(self, message: Message, state: FSMContext) -> bool:
        from platforms.image_menu_flow import can_intercept_text_as_image_prompt

        return await can_intercept_text_as_image_prompt(message, state)


async def _edit_or_answer_photo_status(
    message: Message,
    status_msg: Message | None,
    text: str,
) -> None:
    """Редактирует «Мула в облаках» или шлёт новое сообщение."""
    if status_msg is not None:
        try:
            await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
            return
        except TelegramBadRequest:
            logger.debug("photo status edit failed, sending new message", exc_info=True)
    await message.answer(text, parse_mode=ParseMode.HTML)


async def _delete_photo_service_messages(message: Message, state: FSMContext) -> None:
    """Удаляет «Фото принято…» и прочие подсказки i2i (zero-trash при ошибке)."""
    data = await state.get_data()
    ids = [int(x) for x in (data.get("photo_service_message_ids") or []) if x]
    if not ids:
        return
    bot = deps.bot()
    chat_id = message.chat.id
    for mid in ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except TelegramBadRequest:
            pass
    await state.update_data(photo_service_message_ids=[])


async def _aspect_ratio_from_state(state: FSMContext) -> str:
    data = await state.get_data()
    return normalize_photo_aspect_ratio(data.get("image_aspect_ratio"))


def _image_document_file_id(message: Message) -> str | None:
    """file_id документа, если это изображение (jpg/png/webp/…)."""
    doc = message.document
    if doc is None:
        return None
    mime = (doc.mime_type or "").lower()
    name = (doc.file_name or "").lower()
    if mime.startswith("image/"):
        return doc.file_id
    if name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")):
        return doc.file_id
    return None


def _photo_reference_from_message(message: Message) -> tuple[str | None, str]:
    """(telegram_file_id, caption/промпт) из photo или image-document."""
    caption = (message.caption or "").strip()
    if message.photo:
        return message.photo[-1].file_id, caption
    doc_id = _image_document_file_id(message)
    if doc_id:
        return doc_id, caption
    return None, caption


async def _dispatch_composite_photo_message(
    message: Message,
    state: FSMContext,
    *,
    object_file_id: str,
    prompt: str,
    model_id: str,
    label: str,
    aspect: str,
    base_file_id: str | None,
    base_url: str | None,
    base_bytes: bytes | None,
    base_mime: str,
) -> None:
    data = await state.get_data()
    keep_refine = bool(data.get("refine_from_result"))
    await state.update_data(
        composite_retry_base_id=base_file_id,
        composite_retry_object_id=object_file_id,
        composite_retry_prompt=prompt,
        pending_reference_file_id=None,
        pending_object_file_id=None,
    )
    if not keep_refine:
        await state.update_data(refine_from_result=None)
    await process_photo_prompt_message(
        message,
        state,
        model_id=model_id,
        label=label,
        prompt=prompt,
        telegram_file_id=object_file_id,
        aspect_ratio=aspect,
        composite_refine=True,
        composite_base_file_id=base_file_id,
        composite_base_reference_url=base_url,
        composite_base_reference_bytes=base_bytes,
        composite_base_reference_mime=base_mime,
    )


async def _dispatch_group_multi_ref_photo_message(
    message: Message,
    state: FSMContext,
    *,
    ref_file_ids: list[str],
    prompt: str,
    model_id: str,
    label: str,
    aspect: str,
) -> None:
    from services.photo_multi_ref_routing import MAX_GROUP_REFS

    refs = [(fid or "").strip() for fid in ref_file_ids if (fid or "").strip()][:MAX_GROUP_REFS]
    await state.update_data(
        pending_reference_file_id=None,
        pending_object_file_id=None,
        pending_group_ref_file_ids=[],
        refine_from_result=None,
    )
    await process_photo_prompt_message(
        message,
        state,
        model_id=model_id,
        label=label,
        prompt=prompt,
        aspect_ratio=aspect,
        group_multi_ref=True,
        group_ref_file_ids=refs,
    )


async def _try_dispatch_composite_from_context(
    message: Message,
    state: FSMContext,
    *,
    object_file_id: str,
    prompt: str,
    model_id: str,
    label: str,
    aspect: str,
) -> bool:
    """
    Dual-ref: Image 1 + Image 2 + prompt.
    Refine → base from edit-session (последний результат).
    Первая генерация → base из pending_reference_file_id (первое фото).
    """
    from services.photo_edit_session import (
        get_photo_edit_session,
        resolve_session_result_reference,
        session_has_result_image,
    )

    user = message.from_user
    if user is None or not (prompt or "").strip():
        return False

    data = await state.get_data()
    refine_from_result = bool(data.get("refine_from_result"))
    if not refine_from_result:
        from services.photo_multi_ref_routing import is_composite_merch_intent

        if not is_composite_merch_intent(prompt):
            return False
    pending_object_id = str(data.get("pending_object_file_id") or "").strip()
    pending_base = str(data.get("pending_reference_file_id") or "").strip()

    session = get_photo_edit_session(user.id, peer_id=message.chat.id)
    has_session = session is not None and session_has_result_image(session)

    if refine_from_result and not has_session:
        from services.photo_edit_session import get_or_restore_photo_edit_session

        try:
            session = await get_or_restore_photo_edit_session(user.id, peer_id=message.chat.id)
        except Exception:
            logger.debug("composite refine: DB restore failed uid=%s", user.id, exc_info=True)
            session = None
        has_session = session is not None and session_has_result_image(session)

    base_file_id: str | None = None
    base_url: str | None = None
    base_bytes: bytes | None = None
    base_mime = "image/jpeg"

    if refine_from_result:
        if not has_session:
            return False
        result_ref = resolve_session_result_reference(session)
        base_file_id = result_ref.telegram_file_id
        base_url = result_ref.media_url if not base_file_id else None
        base_bytes = (
            result_ref.reference_image_bytes
            if not base_file_id and not base_url
            else None
        )
        base_mime = result_ref.reference_mime
    elif pending_base and pending_base != object_file_id:
        base_file_id = pending_base
    else:
        return False

    if not base_file_id and not base_url and not base_bytes:
        return False

    await _dispatch_composite_photo_message(
        message,
        state,
        object_file_id=object_file_id,
        prompt=prompt,
        model_id=model_id,
        label=label,
        aspect=aspect,
        base_file_id=base_file_id,
        base_url=base_url,
        base_bytes=base_bytes,
        base_mime=base_mime,
    )
    return True


async def _dispatch_composite_refine_photo(
    message: Message,
    state: FSMContext,
    *,
    object_file_id: str,
    prompt: str,
    model_id: str,
    label: str,
    aspect: str,
) -> bool:
    """Backward-compatible wrapper → ``_try_dispatch_composite_from_context``."""
    return await _try_dispatch_composite_from_context(
        message,
        state,
        object_file_id=object_file_id,
        prompt=prompt,
        model_id=model_id,
        label=label,
        aspect=aspect,
    )


def _album_image_entries(album_messages: list[Message]) -> list[tuple[str, str]]:
    """(file_id, caption) для каждого изображения в альбоме, по порядку message_id."""
    ordered = sorted(album_messages, key=lambda item: item.message_id or 0)
    entries: list[tuple[str, str]] = []
    for item in ordered:
        file_id, caption = _photo_reference_from_message(item)
        if file_id:
            entries.append((file_id, caption))
    return entries


def _album_caption(album_messages: list[Message]) -> str:
    for item in sorted(album_messages, key=lambda m: m.message_id or 0):
        caption = (item.caption or "").strip()
        if caption:
            return caption
    return ""


def _pending_group_ref_ids(data: dict) -> list[str]:
    """Собранные file_id референсов в photo-flow (альбом или по одному)."""
    from services.photo_multi_ref_routing import MAX_GROUP_REFS

    refs: list[str] = []
    for raw in data.get("pending_group_ref_file_ids") or []:
        fid = str(raw or "").strip()
        if fid and fid not in refs:
            refs.append(fid)
    for key in ("pending_reference_file_id", "pending_object_file_id"):
        fid = str(data.get(key) or "").strip()
        if fid and fid not in refs:
            refs.append(fid)
    return refs[:MAX_GROUP_REFS]


async def _store_pending_group_refs(state: FSMContext, file_ids: list[str]) -> list[str]:
    from services.photo_multi_ref_routing import MAX_GROUP_REFS

    refs = [(fid or "").strip() for fid in file_ids if (fid or "").strip()][:MAX_GROUP_REFS]
    await state.update_data(
        pending_group_ref_file_ids=refs,
        pending_reference_file_id=refs[0] if refs else None,
        pending_object_file_id=refs[1] if len(refs) >= 2 else None,
    )
    return refs


async def _append_pending_group_ref(state: FSMContext, file_id: str) -> list[str]:
    data = await state.get_data()
    refs = _pending_group_ref_ids(data)
    fid = (file_id or "").strip()
    if fid and fid not in refs:
        refs.append(fid)
    return await _store_pending_group_refs(state, refs)


async def _clear_pending_group_refs(state: FSMContext) -> None:
    await state.update_data(
        pending_group_ref_file_ids=[],
        pending_reference_file_id=None,
        pending_object_file_id=None,
    )


async def _dispatch_photo_album_message(
    message: Message,
    state: FSMContext,
    album_messages: list[Message],
) -> bool:
    """Альбом: 2+ фото → group multi-ref; composite только при merch-ключах в промпте."""
    from services.photo_multi_ref_routing import (
        MAX_GROUP_REFS,
        MIN_GROUP_REFS,
        should_route_album_as_composite,
    )

    entries = _album_image_entries(album_messages)
    file_ids = [fid for fid, _ in entries]
    caption = _album_caption(album_messages)

    if len(file_ids) < MIN_GROUP_REFS:
        return await _dispatch_photo_reference_message(message, state)

    data = await state.get_data()
    model_id = str(data.get("image_model_id") or "").strip()
    if not model_id:
        await message.answer(msg.TXT_IMAGE_PICK_MODEL_FIRST, parse_mode=ParseMode.HTML)
        return True

    label = str(data.get("image_model_label") or "модель")
    aspect = normalize_photo_aspect_ratio(data.get("image_aspect_ratio"))
    refine_from_result = bool(data.get("refine_from_result"))

    if refine_from_result:
        if len(file_ids) > 1:
            await message.answer(msg.TXT_PHOTO_REFINE_ALBUM_REJECT, parse_mode=ParseMode.HTML)
            return True
        return await _dispatch_photo_reference_message(message, state)

    if caption and should_route_album_as_composite(num_refs=len(file_ids), prompt=caption):
        await _dispatch_composite_photo_message(
            message,
            state,
            object_file_id=file_ids[1],
            prompt=caption,
            model_id=model_id,
            label=label,
            aspect=aspect,
            base_file_id=file_ids[0],
            base_url=None,
            base_bytes=None,
            base_mime="image/jpeg",
        )
        await _clear_pending_group_refs(state)
        return True

    if caption:
        await _dispatch_group_multi_ref_photo_message(
            message,
            state,
            ref_file_ids=file_ids[:MAX_GROUP_REFS],
            prompt=caption,
            model_id=model_id,
            label=label,
            aspect=aspect,
        )
        await _clear_pending_group_refs(state)
        return True

    await _store_pending_group_refs(state, file_ids)
    hint = await message.answer(msg.TXT_PHOTO_ALBUM_WAIT_CAPTION, parse_mode=ParseMode.HTML)
    service_ids = list(data.get("photo_service_message_ids") or [])
    service_ids.append(hint.message_id)
    await state.update_data(photo_service_message_ids=service_ids)
    await state.set_state(UserFlow.waiting_for_photo)
    return True


async def _dispatch_photo_reference_message(message: Message, state: FSMContext) -> bool:
    """
    Фото/файл-картинка в photo-flow: caption в том же сообщении → сразу i2i;
    без caption → двухшаговый режим (pending_reference_file_id).
    """
    file_id, caption = _photo_reference_from_message(message)
    if not file_id:
        return False

    data = await state.get_data()
    model_id = str(data.get("image_model_id") or "").strip()
    if not model_id:
        await message.answer(msg.TXT_IMAGE_PICK_MODEL_FIRST, parse_mode=ParseMode.HTML)
        return True

    label = str(data.get("image_model_label") or "модель")
    aspect = normalize_photo_aspect_ratio(data.get("image_aspect_ratio"))
    refine_from_result = bool(data.get("refine_from_result"))

    if caption:
        if not refine_from_result:
            from services.photo_multi_ref_routing import (
                MAX_GROUP_REFS,
                should_route_album_as_composite,
            )

            pending_refs = _pending_group_ref_ids(data)
            ref_ids = list(pending_refs)
            if file_id and file_id not in ref_ids:
                ref_ids.append(file_id)
            if len(ref_ids) >= 2:
                if should_route_album_as_composite(num_refs=2, prompt=caption):
                    dispatched = await _try_dispatch_composite_from_context(
                        message,
                        state,
                        object_file_id=file_id,
                        prompt=caption,
                        model_id=model_id,
                        label=label,
                        aspect=aspect,
                    )
                    if dispatched:
                        return True
                    await message.answer(msg.TXT_PHOTO_COMPOSITE_FAILED, parse_mode=ParseMode.HTML)
                    return True
                await _dispatch_group_multi_ref_photo_message(
                    message,
                    state,
                    ref_file_ids=ref_ids[:MAX_GROUP_REFS],
                    prompt=caption,
                    model_id=model_id,
                    label=label,
                    aspect=aspect,
                )
                return True

        dispatched = await _try_dispatch_composite_from_context(
            message,
            state,
            object_file_id=file_id,
            prompt=caption,
            model_id=model_id,
            label=label,
            aspect=aspect,
        )
        if dispatched:
            return True
        if refine_from_result:
            await message.answer(msg.TXT_PHOTO_COMPOSITE_FAILED, parse_mode=ParseMode.HTML)
            return True

        await process_photo_prompt_message(
            message,
            state,
            model_id=model_id,
            label=label,
            prompt=caption,
            telegram_file_id=file_id,
            aspect_ratio=aspect,
        )
        return True

    if refine_from_result:
        hint = await message.answer(msg.TXT_PHOTO_REFINE_OBJECT_WAIT, parse_mode=ParseMode.HTML)
        service_ids = list(data.get("photo_service_message_ids") or [])
        service_ids.append(hint.message_id)
        await state.update_data(
            pending_object_file_id=file_id,
            photo_service_message_ids=service_ids,
        )
        await state.set_state(UserFlow.waiting_for_photo)
        return True

    pending_base = str(data.get("pending_reference_file_id") or "").strip()
    pending_object = str(data.get("pending_object_file_id") or "").strip()
    pending_refs = _pending_group_ref_ids(data)
    if pending_refs and file_id and file_id not in pending_refs:
        pending_refs = await _append_pending_group_ref(state, file_id)
    elif not pending_refs and pending_base and pending_base != file_id and not pending_object:
        pending_refs = await _append_pending_group_ref(state, pending_base)
        pending_refs = await _append_pending_group_ref(state, file_id)
    elif not pending_refs:
        pending_refs = await _append_pending_group_ref(state, file_id)

    if len(pending_refs) >= 2:
        hint = await message.answer(msg.TXT_PHOTO_ALBUM_WAIT_CAPTION, parse_mode=ParseMode.HTML)
    else:
        hint = await message.answer(msg.TXT_PHOTO_MULTI_REF_COLLECT, parse_mode=ParseMode.HTML)
    service_ids = list(data.get("photo_service_message_ids") or [])
    service_ids.append(hint.message_id)
    await state.update_data(photo_service_message_ids=service_ids)
    await state.set_state(UserFlow.waiting_for_photo)
    return True


async def try_handle_photo_for_image_generation(
    message: Message,
    state: FSMContext,
    *,
    album_messages: list[Message] | None = None,
) -> bool:
    """
    Перехват фото вне waiting_for_photo (idle / aspect / model pick),
    чтобы caption+i2i не уходили в Нейротекст.
    """
    if album_messages and len(album_messages) > 1:
        file_ids = [fid for fid, _ in _album_image_entries(album_messages)]
        if not file_ids or message.from_user is None:
            return False
        from platforms.telegram_throttling import is_photo_flow_active

        current = await state.get_state()
        photo_states = {
            UserFlow.waiting_for_photo.state,
            UserFlow.waiting_for_image_model_pick.state,
            UserFlow.waiting_for_image_aspect_ratio.state,
        }
        data = await state.get_data()
        model_id = str(data.get("image_model_id") or "").strip()
        in_photo_flow = current in photo_states or is_photo_flow_active(message.from_user.id)
        if not in_photo_flow and not model_id:
            return False
        return await _dispatch_photo_album_message(message, state, album_messages)

    file_id, _caption = _photo_reference_from_message(message)
    if not file_id or message.from_user is None:
        return False

    from platforms.telegram_throttling import is_photo_flow_active

    current = await state.get_state()
    photo_states = {
        UserFlow.waiting_for_photo.state,
        UserFlow.waiting_for_image_model_pick.state,
        UserFlow.waiting_for_image_aspect_ratio.state,
    }
    data = await state.get_data()
    model_id = str(data.get("image_model_id") or "").strip()

    in_photo_flow = current in photo_states or is_photo_flow_active(message.from_user.id)
    if not in_photo_flow and not model_id:
        return False

    return await _dispatch_photo_reference_message(message, state)


from services.photo_edit_session import PhotoEditSession


async def _try_dispatch_sharpen_upscale_refine(
    message: Message,
    state: FSMContext,
    *,
    prompt: str,
) -> bool:
    """«Сделать четче» / upscale after ✏️ Доработать — real OpenRouter upscale, not t2i."""
    from content.keyboards import new_result_keyboard
    from services.openrouter_images import (
        openrouter_images_configured,
        resolve_openrouter_reference_url,
        upscale_openrouter_image_url,
    )
    from services.photo_edit_session import (
        get_or_restore_photo_edit_session,
        is_photo_sharpen_intent,
        persist_photo_edit_session,
        resolve_session_result_reference,
        resolve_sharpen_scale,
        session_has_result_image,
    )

    if not is_photo_sharpen_intent(prompt):
        return False
    if not openrouter_images_configured(settings):
        await message.answer(msg.TXT_GEN_JOB_FAILED)
        return True

    user = message.from_user
    if user is None:
        return False

    session = await get_or_restore_photo_edit_session(user.id, peer_id=message.chat.id)
    if session is None or not session_has_result_image(session):
        await message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
        return True

    result_ref = resolve_session_result_reference(session)
    bot = deps.bot()
    try:
        image_url = await resolve_openrouter_reference_url(
            bot=bot,
            file_id=result_ref.telegram_file_id,
            reference_image_url=result_ref.media_url,
            reference_image_bytes=result_ref.reference_image_bytes,
            reference_mime=result_ref.reference_mime,
        )
    except Exception:
        logger.warning("sharpen refine: failed to resolve image uid=%s", user.id, exc_info=True)
        await message.answer(msg.TXT_UPSCALE_FAILED)
        return True

    if not image_url:
        await message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
        return True

    spend = await billing.spend_upscale(user.id)
    if not spend.ok:
        await message.answer(
            msg.TXT_INSUFFICIENT_BALANCE,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return True

    charge_id = spend.charge.charge_id if spend.charge else ""
    scale = resolve_sharpen_scale(prompt)

    await state.update_data(
        pending_reference_file_id=None,
        pending_object_file_id=None,
        pending_group_ref_file_ids=[],
        refine_from_result=None,
    )

    status = await message.answer(msg.TXT_UPSCALE_PROCESSING)
    chat_id = message.chat.id

    try:
        async with chat_action_loop(bot, chat_id, "upload_document"):
            upscaled_url = await upscale_openrouter_image_url(
                settings,
                image_url,
                scale_value=scale,
            )
        sent = await bot.send_photo(
            chat_id,
            photo=upscaled_url,
            caption=msg.format_photo_result_caption_html(
                session.image_model_label,
                session.user_prompt or prompt,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=new_result_keyboard(),
        )
        tg_file_id = sent.photo[-1].file_id if sent.photo else None
        await persist_photo_edit_session(
            user.id,
            image_model_id=session.image_model_id,
            image_model_label=session.image_model_label,
            aspect_ratio=session.aspect_ratio,
            telegram_file_id=tg_file_id,
            media_url=upscaled_url,
            message_id=sent.message_id,
            chat_id=chat_id,
            platform="telegram",
            user_prompt=session.user_prompt,
            reference_file_id=session.reference_file_id,
            generation_seed=session.generation_seed,
            group_ref_file_ids=session.group_ref_file_ids,
            group_base_prompt=session.group_base_prompt,
        )
        try:
            await status.delete()
        except TelegramBadRequest:
            pass
    except Exception:
        logger.exception("sharpen upscale refine failed uid=%s scale=%s", user.id, scale)
        if charge_id:
            await refund_charge(charge_id)
        await message.answer(msg.TXT_UPSCALE_FAILED)
    return True


async def _dispatch_group_refine_from_session(
    message: Message,
    state: FSMContext,
    *,
    session: PhotoEditSession,
    edit_prompt: str,
    model_id: str,
    label: str,
    aspect: str,
) -> None:
    """Text refine after group photo: i2i on the generated result, not a new 4-ref run."""
    from services.photo_edit_session import (
        clear_awaiting_text_refine,
        build_group_refine_user_prompt,
        resolve_session_result_reference,
        session_has_result_image,
    )

    if not session_has_result_image(session):
        await message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
        return

    result_ref = resolve_session_result_reference(session)
    file_id = result_ref.telegram_file_id
    ref_url = result_ref.media_url if not file_id else None
    ref_bytes = (
        result_ref.reference_image_bytes
        if not file_id and not ref_url
        else None
    )
    if not file_id and not ref_url and not ref_bytes:
        await message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
        return

    combined = build_group_refine_user_prompt(
        session.group_base_prompt or session.user_prompt or "",
        edit_prompt,
    )
    if message.from_user is not None:
        clear_awaiting_text_refine(message.from_user.id)
    await state.update_data(
        pending_reference_file_id=None,
        pending_object_file_id=None,
        pending_group_ref_file_ids=[],
        refine_from_result=None,
    )
    await process_photo_prompt_message(
        message,
        state,
        model_id=model_id,
        label=label,
        prompt=combined,
        telegram_file_id=file_id,
        reference_image_url=ref_url,
        reference_image_bytes=ref_bytes,
        reference_mime=result_ref.reference_mime,
        aspect_ratio=aspect,
        i2i_reference_mode="edit",
    )


async def try_start_photo_edit_from_reply(message: Message, state: FSMContext) -> bool:
    """Reply на сообщение бота с фото → i2i по last_generated_image (15 мин)."""
    from platforms.telegram_quote import is_reply_to_bot_message
    from services.billing.image_pipeline import free_tier_image_model
    from services.photo_edit_session import (
        get_photo_edit_session,
        session_has_group_refs,
        update_photo_edit_session_aspect_ratio,
    )
    from services.photo_intent_parser import resolve_photo_edit_prompt

    if not is_reply_to_bot_message(message):
        return False
    reply = message.reply_to_message
    if reply is None or not reply.photo:
        return False

    prompt = (message.text or "").strip()
    if not prompt:
        return False

    user = message.from_user
    if user is None:
        return False

    session = get_photo_edit_session(user.id)
    file_id = reply.photo[-1].file_id
    data = await state.get_data()

    model_id = (session.image_model_id if session else data.get("image_model_id")) or free_tier_image_model()
    label = (
        session.image_model_label
        if session
        else str(data.get("image_model_label") or "Flux FREE")
    )
    base_aspect = session.aspect_ratio if session else await _aspect_ratio_from_state(state)
    aspect, prompt, aspect_changed = await resolve_photo_edit_prompt(
        prompt,
        current_aspect=base_aspect,
    )
    if aspect_changed:
        await state.update_data(image_aspect_ratio=aspect)
        update_photo_edit_session_aspect_ratio(user.id, aspect)

    await state.update_data(
        image_model_id=model_id,
        image_model_label=label,
        image_aspect_ratio=aspect,
    )
    await state.set_state(UserFlow.waiting_for_photo)
    if session and session_has_group_refs(session):
        await _dispatch_group_refine_from_session(
            message,
            state,
            session=session,
            edit_prompt=prompt,
            model_id=str(model_id),
            label=str(label),
            aspect=aspect,
        )
        return True
    await process_photo_prompt_message(
        message,
        state,
        model_id=str(model_id),
        label=str(label),
        prompt=prompt,
        telegram_file_id=file_id,
        aspect_ratio=aspect,
        i2i_reference_mode="edit",
    )
    return True


async def process_photo_prompt_message(
    message: Message,
    state: FSMContext,
    *,
    model_id: str,
    label: str,
    prompt: str,
    auto_flux: bool = False,
    telegram_file_id: str | None = None,
    reference_image_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
    aspect_ratio: str | None = None,
    skip_status_message: bool = False,
    composite_refine: bool = False,
    composite_base_file_id: str | None = None,
    composite_base_reference_url: str | None = None,
    composite_base_reference_bytes: bytes | None = None,
    composite_base_reference_mime: str = "image/jpeg",
    group_multi_ref: bool = False,
    group_ref_file_ids: list[str] | None = None,
    group_base_prompt: str | None = None,
    i2i_reference_mode: str = "selfie",
) -> None:
    from platforms.image_menu_flow import normalize_image_prompt_text
    from platforms.telegram_throttling import clear_photo_flow, mark_photo_flow

    _ = auto_flux
    user = message.from_user
    if user is None:
        return
    user_id = user.id
    chat_id = message.chat.id
    body = normalize_image_prompt_text(prompt or "")
    ar = normalize_photo_aspect_ratio(aspect_ratio) if aspect_ratio else await _aspect_ratio_from_state(state)

    mark_photo_flow(user_id)

    if not body and not telegram_file_id and not composite_refine and not group_multi_ref:
        await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)
        return

    status_msg: Message | None = None
    if not skip_status_message:
        from services.photo_gen_status import send_photo_gen_status_message

        status_msg = await send_photo_gen_status_message(
            deps.bot(),
            chat_id,
            model_label=label,
            aspect_ratio=ar,
            model_id=model_id,
        )

    try:
        lock = user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            async with chat_action_loop(deps.bot(), chat_id, "upload_photo"):
                pr = await run_photo_generation_turn(
                    settings,
                    deps.bot(),
                    chat_id,
                    user_id,
                    model_id,
                    label,
                    body or prompt,
                    telegram_file_id=telegram_file_id,
                    reference_image_url=reference_image_url,
                    reference_image_bytes=reference_image_bytes,
                    reference_mime=reference_mime,
                    aspect_ratio=ar,
                    composite_refine=composite_refine,
                    composite_base_file_id=composite_base_file_id,
                    composite_base_reference_url=composite_base_reference_url,
                    composite_base_reference_bytes=composite_base_reference_bytes,
                    group_multi_ref=group_multi_ref,
                    group_ref_file_ids=group_ref_file_ids,
                    group_base_prompt=group_base_prompt,
                    i2i_reference_mode=i2i_reference_mode,
                )
    except ValueError as exc:
        logger.warning("photo prompt: invalid reference wiring uid=%s: %s", user_id, exc)
        await _edit_or_answer_photo_status(
            message,
            status_msg,
            msg.TXT_PHOTO_COMPOSITE_FAILED,
        )
        clear_photo_flow(user_id)
        return
    except Exception:
        logger.exception("photo prompt: billing/enqueue failed uid=%s", user_id)
        await _edit_or_answer_photo_status(
            message,
            status_msg,
            msg.TXT_FREE_IMAGE_CASCADE_FAILED,
        )
        await _delete_photo_service_messages(message, state)
        await state.clear()
        clear_photo_flow(user_id)
        return

    if pr.outcome is PhotoGenOutcome.NEED_PROMPT:
        await _edit_or_answer_photo_status(
            message,
            status_msg,
            msg.TXT_CREATE_IMAGE_AFTER_MODEL,
        )
        return
    if pr.outcome is PhotoGenOutcome.GLOBAL_FREE_IMAGE_CAP:
        await _edit_or_answer_photo_status(
            message,
            status_msg,
            msg.TXT_FREE_IMAGE_GLOBAL_CAP,
        )
        await _delete_photo_service_messages(message, state)
        await state.clear()
        clear_photo_flow(user_id)
        return
    if pr.outcome is PhotoGenOutcome.INSUFFICIENT_BALANCE:
        if status_msg is not None:
            try:
                await status_msg.delete()
            except TelegramBadRequest:
                pass
        await message.answer(
            msg.TXT_INSUFFICIENT_BALANCE,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await _delete_photo_service_messages(message, state)
        await state.clear()
        clear_photo_flow(user_id)
        return
    if pr.outcome is PhotoGenOutcome.DAILY_LIMIT_EXCEEDED:
        if status_msg is not None:
            try:
                await status_msg.delete()
            except TelegramBadRequest:
                pass
        await message.answer(
            msg.TXT_PHOTO_DAILY_LIMIT.format(limit=settings.free_daily_photo_limit),
            reply_markup=invite_limit_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await _delete_photo_service_messages(message, state)
        await state.clear()
        clear_photo_flow(user_id)
        return
    if pr.outcome is PhotoGenOutcome.FREE_IMAGE_MODEL_BLOCKED:
        await _edit_or_answer_photo_status(
            message,
            status_msg,
            msg.TXT_FREE_IMAGE_MODEL_BLOCKED,
        )
        await _delete_photo_service_messages(message, state)
        await state.clear()
        clear_photo_flow(user_id)
        return

    eq = pr.enqueue
    if eq is None:
        await _edit_or_answer_photo_status(
            message,
            status_msg,
            msg.TXT_GEN_JOB_FAILED,
        )
        await _delete_photo_service_messages(message, state)
        await state.clear()
        clear_photo_flow(user_id)
        return

    if eq.composite_refine:
        await state.update_data(
            composite_retry_base_id=eq.composite_base_file_id,
            composite_retry_object_id=eq.telegram_file_id,
            composite_retry_prompt=eq.prompt,
        )

    data = await state.get_data()
    cleanup_ids = tuple(int(x) for x in (data.get("photo_service_message_ids") or []) if x)
    await state.update_data(photo_service_message_ids=[])

    fire_photo_job(
        deps.bot(),
        chat_id,
        user_id,
        eq.image_model_id,
        eq.model_label,
        eq.prompt,
        eq.used_daily_slot,
        eq.charged_crystals,
        priority=eq.priority,
        billing_charge_id=eq.billing_charge_id,
        telegram_file_id=eq.telegram_file_id,
        reference_image_url=eq.reference_image_url,
        reference_image_bytes=eq.reference_image_bytes,
        reference_mime=eq.reference_mime,
        aspect_ratio=eq.aspect_ratio,
        status_message_id=status_msg.message_id if status_msg is not None else None,
        cleanup_message_ids=cleanup_ids,
        composite_refine=eq.composite_refine,
        composite_base_file_id=eq.composite_base_file_id,
        composite_base_reference_url=eq.composite_base_reference_url,
        composite_base_reference_bytes=eq.composite_base_reference_bytes,
        group_multi_ref=eq.group_multi_ref,
        group_ref_file_ids=eq.group_ref_file_ids,
        group_base_prompt=eq.group_base_prompt,
        i2i_reference_mode=eq.i2i_reference_mode,
    )
    if pr.vip_priority:
        await message.answer(msg.TXT_GEN_STATUS_VIP)
    from platforms.image_menu_flow import clear_image_model_menu_pending

    await clear_image_model_menu_pending(state)
    await state.update_data(
        pending_reference_file_id=None,
        pending_object_file_id=None,
        image_aspect_ratio=ar,
    )
    await state.set_state(UserFlow.waiting_for_photo)
    mark_photo_flow(user_id)


@router.message(StateFilter(None), REPLY_TO_BOT_FILTER, F.text)
async def photo_edit_reply_idle(message: Message, state: FSMContext) -> None:
    if await try_start_photo_edit_from_reply(message, state):
        return


@router.message(UserFlow.waiting_for_image_aspect_ratio, F.text)
async def image_aspect_ratio_pick_text(message: Message, state: FSMContext) -> None:
    if await _dispatch_nav_or_none(message, state):
        return
    from services.photo_aspect_ratio import format_aspect_ratio_picker_html

    await message.answer(
        format_aspect_ratio_picker_html(),
        reply_markup=image_aspect_ratio_menu(),
        parse_mode=ParseMode.HTML,
    )


@router.message(UserFlow.waiting_for_image_model_pick, F.text)
async def image_model_pick_text(message: Message, state: FSMContext) -> None:
    from platforms.image_menu_flow import handle_pending_image_menu_text

    await handle_pending_image_menu_text(message, state)


@router.message(PendingImageMenuTextFilter(), F.text)
async def image_menu_pending_text(message: Message, state: FSMContext) -> None:
    from platforms.image_menu_flow import handle_pending_image_menu_text

    await handle_pending_image_menu_text(message, state)


@router.message(UserFlow.waiting_for_photo, F.photo | F.document)
async def photo_process_with_image(
    message: Message,
    state: FSMContext,
    album_messages: list[Message] | None = None,
) -> None:
    if await _dispatch_nav_or_none(message, state):
        return
    if album_messages and len(album_messages) > 1:
        if await _dispatch_photo_album_message(message, state, album_messages):
            return
    if not await _dispatch_photo_reference_message(message, state):
        await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL, parse_mode=ParseMode.HTML)


@router.message(UserFlow.waiting_for_image_model_pick, F.photo | F.document)
@router.message(UserFlow.waiting_for_image_aspect_ratio, F.photo | F.document)
async def photo_process_during_model_setup(
    message: Message,
    state: FSMContext,
    album_messages: list[Message] | None = None,
) -> None:
    if await _dispatch_nav_or_none(message, state):
        return
    if album_messages and len(album_messages) > 1:
        await _dispatch_photo_album_message(message, state, album_messages)
        return
    await _dispatch_photo_reference_message(message, state)


@router.message(UserFlow.waiting_for_photo, F.text)
async def photo_process(message: Message, state: FSMContext) -> None:
    if await _dispatch_nav_or_none(message, state):
        return
    from services.photo_edit_session import (
        clear_awaiting_text_refine,
        get_or_restore_photo_edit_session,
        get_photo_edit_session,
        resolve_session_result_reference,
        session_has_group_refs,
        session_has_result_image,
        update_photo_edit_session_aspect_ratio,
    )
    from services.photo_intent_parser import resolve_photo_edit_prompt

    data = await state.get_data()
    model_id = data.get("image_model_id", "")
    label = data.get("image_model_label", "модель")
    aspect = normalize_photo_aspect_ratio(data.get("image_aspect_ratio"))
    prompt = (message.text or "").strip()
    if message.from_user is not None:
        from platforms.media_group_middleware import album_collection_pending

        if album_collection_pending(message.from_user.id):
            await asyncio.sleep(1.15)
            data = await state.get_data()

    session_hint = None
    if message.from_user is not None:
        session_hint = await get_or_restore_photo_edit_session(
            message.from_user.id,
            peer_id=message.chat.id,
        )

    pending_file_id = str(data.get("pending_reference_file_id") or "").strip()
    pending_object_id = str(data.get("pending_object_file_id") or "").strip()
    pending_refs = _pending_group_ref_ids(data)
    refine_from_result = bool(data.get("refine_from_result"))
    if not refine_from_result and session_hint is not None and session_hint.awaiting_text_refine:
        refine_from_result = True
        if not str(model_id or "").strip() and session_hint.image_model_id:
            model_id = session_hint.image_model_id
            label = session_hint.image_model_label
            aspect = session_hint.aspect_ratio
            await state.update_data(
                image_model_id=model_id,
                image_model_label=label,
                image_aspect_ratio=aspect,
                refine_from_result=True,
                pending_reference_file_id=None,
                pending_object_file_id=None,
                pending_group_ref_file_ids=[],
            )

    if (
        len(pending_refs) >= 2
        and prompt
        and not refine_from_result
    ):
        from services.photo_multi_ref_routing import (
            MAX_GROUP_REFS,
            should_route_album_as_composite,
        )

        aspect, prompt, aspect_changed = await resolve_photo_edit_prompt(
            prompt,
            current_aspect=aspect,
        )
        if aspect_changed and message.from_user is not None:
            await state.update_data(image_aspect_ratio=aspect)

        if should_route_album_as_composite(num_refs=2, prompt=prompt):
            dispatched = await _try_dispatch_composite_from_context(
                message,
                state,
                object_file_id=pending_refs[1],
                prompt=prompt,
                model_id=str(model_id),
                label=str(label),
                aspect=aspect,
            )
            if dispatched:
                await _clear_pending_group_refs(state)
                return
            await message.answer(msg.TXT_PHOTO_COMPOSITE_FAILED, parse_mode=ParseMode.HTML)
            return

        await _dispatch_group_multi_ref_photo_message(
            message,
            state,
            ref_file_ids=pending_refs[:MAX_GROUP_REFS],
            prompt=prompt,
            model_id=str(model_id),
            label=str(label),
            aspect=aspect,
        )
        await _clear_pending_group_refs(state)
        return

    if refine_from_result and pending_object_id and prompt:
        if message.from_user is None:
            return
        aspect, prompt, aspect_changed = await resolve_photo_edit_prompt(
            prompt,
            current_aspect=aspect,
        )
        if aspect_changed:
            await state.update_data(image_aspect_ratio=aspect)
            update_photo_edit_session_aspect_ratio(message.from_user.id, aspect)

        dispatched = await _try_dispatch_composite_from_context(
            message,
            state,
            object_file_id=pending_object_id,
            prompt=prompt,
            model_id=str(model_id),
            label=str(label),
            aspect=aspect,
        )
        if dispatched:
            return
        await message.answer(msg.TXT_PHOTO_COMPOSITE_FAILED, parse_mode=ParseMode.HTML)
        return

    if pending_file_id or refine_from_result:
        aspect, prompt, aspect_changed = await resolve_photo_edit_prompt(
            prompt,
            current_aspect=aspect,
        )
        if aspect_changed and message.from_user is not None:
            await state.update_data(image_aspect_ratio=aspect)
            update_photo_edit_session_aspect_ratio(message.from_user.id, aspect)

        if refine_from_result and prompt:
            if await _try_dispatch_sharpen_upscale_refine(message, state, prompt=prompt):
                return

        file_id: str | None = None
        ref_url: str | None = None
        ref_bytes: bytes | None = None
        ref_mime = "image/jpeg"
        session = None
        if message.from_user is not None:
            if refine_from_result:
                from services.photo_edit_session import get_or_restore_photo_edit_session

                session = await get_or_restore_photo_edit_session(
                    message.from_user.id,
                    peer_id=message.chat.id,
                )
            else:
                session = get_photo_edit_session(message.from_user.id, peer_id=message.chat.id)
                file_id = pending_file_id or None

            if refine_from_result and session and session_has_result_image(session):
                result_ref = resolve_session_result_reference(session)
                file_id = result_ref.telegram_file_id
                if not file_id:
                    ref_url = result_ref.media_url
                    ref_bytes = result_ref.reference_image_bytes
                    ref_mime = result_ref.reference_mime
            elif not refine_from_result and session and session_has_result_image(session):
                result_ref = resolve_session_result_reference(session)
                file_id = file_id or result_ref.telegram_file_id
                if not file_id:
                    ref_url = result_ref.media_url
                    ref_bytes = result_ref.reference_image_bytes
                    ref_mime = result_ref.reference_mime

        if not file_id and not ref_url and not ref_bytes:
            await message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
            return

        if session and session_has_group_refs(session):
            if message.from_user is not None:
                clear_awaiting_text_refine(message.from_user.id)
            await _dispatch_group_refine_from_session(
                message,
                state,
                session=session,
                edit_prompt=prompt,
                model_id=str(model_id),
                label=str(label),
                aspect=aspect,
            )
            return

        if message.from_user is not None:
            clear_awaiting_text_refine(message.from_user.id)
        await state.update_data(pending_reference_file_id=None, refine_from_result=None)
        await process_photo_prompt_message(
            message,
            state,
            model_id=model_id,
            label=label,
            prompt=prompt,
            telegram_file_id=file_id,
            reference_image_url=ref_url,
            reference_image_bytes=ref_bytes,
            reference_mime=ref_mime,
            aspect_ratio=aspect,
            i2i_reference_mode="edit" if refine_from_result else "selfie",
        )
        return

    if (
        prompt
        and message.from_user is not None
        and session_hint is not None
        and session_hint.awaiting_text_refine
        and session_has_result_image(session_hint)
    ):
        result_ref = resolve_session_result_reference(session_hint)
        file_id = result_ref.telegram_file_id
        ref_url = result_ref.media_url if not file_id else None
        ref_bytes = (
            result_ref.reference_image_bytes
            if not file_id and not ref_url
            else None
        )
        if file_id or ref_url or ref_bytes:
            clear_awaiting_text_refine(message.from_user.id)
            await state.update_data(
                pending_reference_file_id=None,
                pending_object_file_id=None,
                pending_group_ref_file_ids=[],
                refine_from_result=None,
            )
            await process_photo_prompt_message(
                message,
                state,
                model_id=str(model_id or session_hint.image_model_id),
                label=str(label or session_hint.image_model_label),
                prompt=prompt,
                telegram_file_id=file_id,
                reference_image_url=ref_url,
                reference_image_bytes=ref_bytes,
                reference_mime=result_ref.reference_mime,
                aspect_ratio=aspect,
                i2i_reference_mode="edit",
            )
            return

    await process_photo_prompt_message(
        message,
        state,
        model_id=model_id,
        label=label,
        prompt=prompt,
        aspect_ratio=aspect,
    )


@router.message(UserFlow.waiting_for_photo)
async def photo_process_need_text(message: Message) -> None:
    await message.answer(msg.TXT_CREATE_IMAGE_AFTER_MODEL)


@router.message(
    UserFlow.waiting_for_text_prompt,
    F.text | F.photo | F.document | REPLY_TO_BOT_FILTER,
)
async def text_role_process(message: Message, state: FSMContext) -> None:
    from platforms.neurotext_input import handle_neurotext_user_message

    await handle_neurotext_user_message(message, state)


def _assistant_text_from_callback_message(message: object) -> str:
    """Текст ответа бота, под которым нажали кнопку (якорь темы follow-up)."""
    html = getattr(message, "html_text", None)
    if isinstance(html, str) and html.strip():
        return html.strip()
    text = getattr(message, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    caption = getattr(message, "caption", None)
    if isinstance(caption, str) and caption.strip():
        return caption.strip()
    return ""


def _hint_user_turn(*, root_user_prompt: str, label: str) -> str:
    """User-turn для модели: корневой вопрос turn + раскрытая подпись кнопки."""
    from services.standard_suggested_replies import expand_suggested_reply_prompt

    expanded = expand_suggested_reply_prompt(label)
    root = (root_user_prompt or "").strip()
    if root:
        return f"По теме «{root}»: {expanded}"
    return expanded


async def _strip_callback_keyboard(message: object) -> None:
    """Снимает inline-клавиатуру, чтобы не было повторных кликов во время генерации."""
    edit = getattr(message, "edit_reply_markup", None)
    if edit is None:
        return
    try:
        await edit(reply_markup=None)
    except Exception:
        logger.debug("suggested_reply: edit_reply_markup failed", exc_info=True)


@router.callback_query(
    F.data.startswith(msg.CB_HINT_BTN_PREFIX)
    | F.data.startswith(msg.CB_CHAT_HINT_PREFIX)
    | F.data.startswith(msg.CB_STD_REPLY_PREFIX)
)
async def cb_standard_suggested_reply(callback: CallbackQuery, state: FSMContext) -> None:
    """Suggested Reply → тот же пайплайн + списание 1⚡/1💎.

    Основной путь: ``btn:<idx>:<action_uuid>`` + HintSession
    (body / labels / root_user_prompt в кэше).

    Legacy soft-fallback (кнопки в старых сообщениях чата):
    ``chat_hint:<текст>``, ``std_reply:<idx>:<context_id>``.
    """
    from platforms.neurotext_flow import ensure_neurotext_waiting_state
    from platforms.neurotext_input import handle_neurotext_user_message
    from services.billing.chat_pipeline import can_afford_role_minimum
    from services.billing.store import load_user_billing
    from services.context_summarize import focus_anchor_for_followup
    from services.god_mode import billing_bypass
    from services.standard_suggested_replies import (
        FREE_FALLBACK_SUGGESTED_REPLIES,
        build_standard_zero_balance_keyboard,
        expand_suggested_reply_prompt,
        resolve_hint_session,
        parse_chat_hint_callback,
        parse_hint_btn_callback,
        parse_std_reply_callback,
        resolve_suggested_reply,
        resolve_suggested_reply_latest,
    )

    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    data = callback.data or ""
    user_id = callback.from_user.id
    label: str | None = None
    anchor: str | None = None
    follow_up: str | None = None

    # --- 1) Stateful HintSession: btn:<idx>:<uuid> ---
    if data.startswith(msg.CB_HINT_BTN_PREFIX):
        parsed_btn = parse_hint_btn_callback(data)
        if parsed_btn is None:
            await callback.answer()
            return
        index, action_uuid = parsed_btn
        session = await resolve_hint_session(action_uuid, user_id=user_id)
        if session is None:
            await callback.answer(
                "Кнопка устарела, отправьте новый запрос",
                show_alert=True,
            )
            return
        if index < 0 or index >= len(session.labels):
            await callback.answer(
                "Кнопка устарела, отправьте новый запрос",
                show_alert=True,
            )
            return
        label = session.labels[index]
        focused = focus_anchor_for_followup(session.body, label)
        follow_up = _hint_user_turn(
            root_user_prompt=session.root_user_prompt,
            label=label,
        )
        anchor = focused or session.body or None

    # --- 2) Legacy FREE: chat_hint:<текст> ---
    elif data.startswith(msg.CB_CHAT_HINT_PREFIX):
        label = parse_chat_hint_callback(data)
        if label:
            follow_up = expand_suggested_reply_prompt(label)
            anchor = _assistant_text_from_callback_message(callback.message) or None

    # --- 3) Legacy paid: std_reply:<idx>:<context_id> ---
    elif data.startswith(msg.CB_STD_REPLY_PREFIX):
        parsed = parse_std_reply_callback(data)
        if parsed is None:
            await callback.answer()
            return
        index, context_id = parsed
        label = resolve_suggested_reply(context_id, index, user_id=user_id)
        if not label:
            label = resolve_suggested_reply_latest(user_id, index)
        if not label and 0 <= index < len(FREE_FALLBACK_SUGGESTED_REPLIES):
            # После рестарта кэш пуст — мягкий FREE-фолбэк по индексу.
            label = FREE_FALLBACK_SUGGESTED_REPLIES[index]
        if label:
            follow_up = expand_suggested_reply_prompt(label)
            anchor = _assistant_text_from_callback_message(callback.message) or None
    else:
        await callback.answer()
        return

    if not label or not follow_up:
        await callback.answer(
            "Кнопка устарела, отправьте новый запрос",
            show_alert=True,
        )
        return

    if not billing_bypass(user_id):
        user = await load_user_billing(user_id)
        if not can_afford_role_minimum(user, "standard"):
            await callback.answer()
            await callback.message.answer(
                msg.TXT_STD_REPLY_ZERO_BALANCE,
                reply_markup=build_standard_zero_balance_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

    # Telegram-гигиена: сразу гасим «часики», потом снимаем кнопки.
    await callback.answer()
    await _strip_callback_keyboard(callback.message)

    await state.update_data(text_role="standard", pending_chat_hint=follow_up)
    await ensure_neurotext_waiting_state(state)
    try:
        await handle_neurotext_user_message(
            callback.message,
            state,
            forced_user_text=follow_up,
            forced_user_id=user_id,
            anchor_assistant_text=anchor or None,
        )
    except Exception:
        logger.exception(
            "cb_standard_suggested_reply failed uid=%s label=%r",
            user_id,
            (label or "")[:80],
        )
        try:
            await callback.message.answer(
                "⚠️ Не удалось обработать кнопку. Напишите вопрос текстом.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


@router.message(UserFlow.waiting_for_text_prompt)
async def text_role_unsupported(message: Message) -> None:
    await message.answer(
        "🤖 В ИИ-Ассистенте можно отправить <b>текст</b>, <b>фото</b> "
        "или файл <b>.txt / .csv / .pdf / .docx</b>.",
        parse_mode=ParseMode.HTML,
    )


@router.message(
    WBAuditingStates.wait_for_tax,
    F.document | F.text,
)
async def wb_audit_wait_for_tax_guard(message: Message) -> None:
    await message.answer(msg.TXT_AUDIT_WB_TAX_REQUIRED, parse_mode=ParseMode.HTML)


@router.message(
    OzonAuditingStates.wait_for_xlsx,
    YandexAuditingStates.wait_for_xlsx,
    OneCAuditingStates.wait_for_xlsx,
    F.document | F.text,
)
async def marketplace_audit_file_process(message: Message, state: FSMContext) -> None:
    """Финансовый аудит Ozon / Яндекс / 1С: ожидание .xlsx / .csv."""
    from platforms.neurotext_input import handle_neurotext_user_message

    await handle_neurotext_user_message(message, state, keep_waiting_state=True)


@router.message(WBAuditingStates.wait_for_xlsx, F.text)
async def wb_audit_wait_for_xlsx_text(message: Message) -> None:
    await message.answer(msg.TXT_AUDIT_WB_WAIT_FOR_FILE, parse_mode=ParseMode.HTML)


@router.message(UserFlow.waiting_for_video, F.text)
async def video_process(message: Message, state: FSMContext) -> None:
    if await _dispatch_nav_or_none(message, state):
        return
    user_id = message.from_user.id
    prompt = (message.text or "").strip()
    data = await state.get_data()
    scenario_id = data.get("video_scenario_id") or "video_pro_5sec"
    vr = await run_video_scenario_turn(
        settings,
        bot,
        message.chat.id,
        user_id,
        str(scenario_id),
        user_prompt=prompt,
    )
    await _reply_video_gen_result(message, vr, state)

@router.message(UserFlow.waiting_for_video_prank_photo, F.photo)
async def video_prank_photo_process(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    scenario_id = str(data.get("video_scenario_id") or "")
    caption = (message.caption or "").strip()
    vr = await run_video_scenario_turn(
        settings,
        bot,
        message.chat.id,
        uid,
        scenario_id,
        user_prompt=caption,
        telegram_file_id=message.photo[-1].file_id,
    )
    await _reply_video_gen_result(message, vr, state)

@router.message(UserFlow.waiting_for_video)
async def video_need_text(message: Message) -> None:
    await message.answer(msg.TXT_VIDEO_NEED_PROMPT)

@router.message(UserFlow.waiting_for_video_prank_photo)
async def video_prank_need_photo(message: Message) -> None:
    await message.answer(msg.TXT_VIDEO_NEED_PHOTO, parse_mode=ParseMode.HTML)

@router.message(UserFlow.waiting_for_animate, F.photo)
async def animate_photo_process(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    large_photo_file_id = message.photo[-1].file_id
    await state.clear()
    ar = await run_animate_generation_turn(
        uid=uid,
        telegram_file_id=large_photo_file_id,
        bot=message.bot,
        chat_id=message.chat.id,
        settings=settings,
    )
    if ar.outcome is AnimateGenOutcome.NEED_PHOTO:
        await message.answer(msg.TXT_CREATE_ANIMATE_HINT)
        return
    if ar.outcome is AnimateGenOutcome.FREE_PREMIUM_BLOCKED:
        from platforms.telegram_utils import send_free_create_blocked

        await send_free_create_blocked(message)
        return
    if ar.outcome is AnimateGenOutcome.FORBIDDEN_BY_TARIFF:
        await message.answer(
            msg.TXT_UPGRADE_TO_ULTRA,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if ar.outcome is AnimateGenOutcome.INSUFFICIENT_BALANCE:
        await message.answer(
            msg.TXT_INSUFFICIENT_BALANCE,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )

@router.message(UserFlow.waiting_for_animate)
async def animate_need_photo(message: Message) -> None:
    await message.answer(msg.TXT_CREATE_ANIMATE_HINT)

@router.message(UserFlow.waiting_for_upscale_photo, F.photo)
async def upscale_process(message: Message, state: FSMContext) -> None:
    from services.openrouter_images import (
        openrouter_images_configured,
        resolve_openrouter_reference_url,
        upscale_openrouter_image_url,
    )

    uid = message.from_user.id
    spend = await billing.spend_upscale(uid)
    if not spend.ok:
        await message.answer(
            msg.TXT_INSUFFICIENT_BALANCE,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        await state.clear()
        return
    if not openrouter_images_configured(settings):
        await message.answer(msg.TXT_GEN_JOB_FAILED)
        await state.clear()
        return

    upscale_charge_id = spend.charge.charge_id if spend.charge else ""
    await message.answer(msg.TXT_UPSCALE_PROCESSING)
    bot = deps.bot()
    photo_id = message.photo[-1].file_id
    try:
        image_url = await resolve_openrouter_reference_url(bot=bot, file_id=photo_id)
        async with chat_action_loop(bot, message.chat.id, "upload_document"):
            upscaled_url = await upscale_openrouter_image_url(
                settings,
                image_url,
                scale_value=2,
            )
        await bot.send_document(
            message.chat.id,
            document=upscaled_url,
            caption=msg.TXT_UPSCALE_DONE,
        )
    except Exception:
        logger.exception("upscale_failed user_id=%s", uid)
        if upscale_charge_id:
            await refund_charge(upscale_charge_id)
        await message.answer(msg.TXT_UPSCALE_FAILED)
    finally:
        await state.clear()

@router.message(UserFlow.waiting_for_upscale_photo)
async def upscale_need_photo(message: Message) -> None:
    await message.answer(msg.TXT_UPSCALE_HINT)

@router.message(Command("profile"))
async def profile(message: Message) -> None:
    from platforms.telegram_keyboards import cabinet_keyboard_for_user

    view = await build_cabinet_view(settings, message.from_user.id)
    await message.answer(
        view.text,
        reply_markup=await cabinet_keyboard_for_user(message.from_user.id),
        parse_mode=ParseMode.HTML,
    )

