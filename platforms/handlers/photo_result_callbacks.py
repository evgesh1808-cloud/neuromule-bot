"""Callback-кнопки под результатом генерации фото (@chatcom UX)."""

from __future__ import annotations

import logging
import random

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from config import settings
from content import messages as msg
from content.inline_keyboards import result_format_submenu_keyboard
from content.keyboards import new_result_keyboard, result_upscale_submenu_keyboard
from platforms.handlers import deps
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_states import UserFlow
from services import payments_catalog as paycat
from services.openrouter_images import (
    openrouter_images_configured,
    resolve_openrouter_reference_url,
    upscale_openrouter_image_url,
)
from services.generation_jobs import fire_photo_job
from services.photo_aspect_ratio import aspect_ratio_from_callback_suffix, normalize_photo_aspect_ratio
from services.photo_edit_session import (
    build_format_change_prompt,
    get_photo_edit_session,
    resolve_session_result_reference,
    session_has_result_image,
    update_photo_edit_session_aspect_ratio,
)
from services.photo_gen_status import send_photo_gen_status_message
from services.repository import get_user_row, try_consume_crystals
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn

logger = logging.getLogger(__name__)

router = Router()

UPSCALE_X2_COST = 1
UPSCALE_X4_COST = 3


async def _restore_result_keyboard(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=new_result_keyboard())
    except TelegramBadRequest:
        pass


async def _resolve_session_upscale_source(session: object) -> str | None:
    from services.photo_edit_session import PhotoEditSession

    if not isinstance(session, PhotoEditSession):
        return None
    ref = resolve_session_result_reference(session)
    bot = deps.bot()
    try:
        return await resolve_openrouter_reference_url(
            bot=bot,
            file_id=ref.telegram_file_id,
            reference_image_url=ref.media_url,
            reference_image_bytes=ref.reference_image_bytes,
            reference_mime=ref.reference_mime,
        )
    except Exception:
        logger.warning("upscale: failed to resolve session image ref uid=%s", session.user_id, exc_info=True)
        return None


async def _rerun_from_session(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    aspect_ratio: str | None = None,
    format_only: bool = False,
) -> None:
    user = callback.from_user
    if user is None or callback.message is None:
        return

    session = get_photo_edit_session(user.id, peer_id=callback.message.chat.id)
    if session is None or not (session.user_prompt or "").strip():
        await callback.message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
        return

    if not session_has_result_image(session):
        await callback.message.answer(msg.TXT_PHOTO_REFINE_EXPIRED)
        return

    result_ref = resolve_session_result_reference(session)
    ar = normalize_photo_aspect_ratio(aspect_ratio or session.aspect_ratio)
    model_id = session.image_model_id
    label = session.image_model_label

    if format_only:
        ref_file_id = result_ref.telegram_file_id
        ref_url = result_ref.media_url if not ref_file_id else None
        ref_bytes = result_ref.reference_image_bytes if not ref_file_id and not ref_url else None
        ref_mime = result_ref.reference_mime
        prompt = build_format_change_prompt(session.user_prompt or "", ar)
        seed = session.generation_seed
    else:
        ref_file_id = session.reference_file_id
        ref_url = session.media_url if not ref_file_id else None
        ref_bytes = None
        ref_mime = session.reference_mime
        prompt = session.user_prompt or ""
        seed = session.generation_seed
        if not ref_file_id and not ref_url:
            ref_file_id = result_ref.telegram_file_id
            ref_url = result_ref.media_url if not ref_file_id else None
            ref_bytes = result_ref.reference_image_bytes if not ref_file_id and not ref_url else None
            ref_mime = result_ref.reference_mime

    bot = deps.bot()
    chat_id = callback.message.chat.id
    status_msg = await send_photo_gen_status_message(
        bot,
        chat_id,
        model_label=label,
        aspect_ratio=ar,
        model_id=model_id,
    )

    try:
        pr = await run_photo_generation_turn(
            settings,
            bot,
            chat_id,
            user.id,
            model_id,
            label,
            prompt,
            telegram_file_id=ref_file_id,
            reference_image_url=ref_url,
            reference_image_bytes=ref_bytes,
            reference_mime=ref_mime,
            aspect_ratio=ar,
        )
    except Exception:
        logger.exception("photo repeat: billing failed uid=%s", user.id)
        if status_msg is not None:
            try:
                await status_msg.delete()
            except TelegramBadRequest:
                pass
        await callback.message.answer(msg.TXT_GEN_JOB_FAILED)
        return

    if pr.outcome is not PhotoGenOutcome.SUCCESS or pr.enqueue is None:
        if status_msg is not None:
            try:
                await status_msg.delete()
            except TelegramBadRequest:
                pass
        if pr.outcome is PhotoGenOutcome.INSUFFICIENT_BALANCE:
            await callback.message.answer(
                msg.TXT_INSUFFICIENT_BALANCE,
                reply_markup=paycat.shop_packages_keyboard(),
            )
        elif pr.outcome is PhotoGenOutcome.DAILY_LIMIT_EXCEEDED:
            await callback.message.answer(
                msg.TXT_PHOTO_DAILY_LIMIT.format(limit=settings.free_daily_photo_limit),
            )
        else:
            await callback.message.answer(msg.TXT_GEN_JOB_FAILED)
        return

    eq = pr.enqueue
    fire_photo_job(
        bot,
        chat_id,
        user.id,
        eq.image_model_id,
        eq.model_label,
        eq.prompt,
        eq.used_daily_slot,
        eq.charged_crystals,
        priority=eq.priority,
        billing_charge_id=eq.billing_charge_id,
        telegram_file_id=eq.telegram_file_id or ref_file_id,
        reference_image_url=eq.reference_image_url or ref_url,
        reference_image_bytes=eq.reference_image_bytes or ref_bytes,
        reference_mime=eq.reference_mime or ref_mime,
        aspect_ratio=eq.aspect_ratio,
        status_message_id=status_msg.message_id if status_msg is not None else None,
        generation_seed=seed or random.randint(1, 2_000_000_000),
    )
    await state.update_data(
        image_model_id=model_id,
        image_model_label=label,
        image_aspect_ratio=ar,
    )
    await state.set_state(UserFlow.waiting_for_photo)


@router.callback_query(F.data == msg.CB_RESULT_UPSCALE)
async def result_upscale_menu(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=result_upscale_submenu_keyboard())
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == msg.CB_RESULT_CHANGE_FORMAT)
async def result_change_format_menu(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=result_format_submenu_keyboard())
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == msg.CB_RESULT_GRID_BACK)
async def result_grid_back(callback: CallbackQuery) -> None:
    await _restore_result_keyboard(callback)
    await callback.answer()


@router.callback_query(F.data == msg.CB_RESULT_REPEAT_PHOTO)
async def result_repeat_photo(callback: CallbackQuery, state: FSMContext) -> None:
    user = callback.from_user
    if user is None or callback.message is None:
        await callback.answer()
        return
    session = get_photo_edit_session(user.id, peer_id=callback.message.chat.id)
    if session is None or not (session.user_prompt or "").strip():
        await callback.answer(msg.TXT_PHOTO_REFINE_EXPIRED, show_alert=True)
        return
    await callback.answer(msg.TXT_RESULT_REPEAT_STARTED)
    await _rerun_from_session(callback, state)


@router.callback_query(F.data.startswith(msg.CB_RES_FMT_PREFIX))
async def result_pick_format(callback: CallbackQuery, state: FSMContext) -> None:
    suffix = (callback.data or "")[len(msg.CB_RES_FMT_PREFIX) :].strip()
    aspect = aspect_ratio_from_callback_suffix(suffix)
    if aspect is None:
        await callback.answer(msg.TXT_UNKNOWN_IMAGE_MODEL, show_alert=True)
        return

    user = callback.from_user
    if user is not None:
        update_photo_edit_session_aspect_ratio(user.id, aspect)
        await state.update_data(image_aspect_ratio=aspect)

    await callback.answer()
    await _restore_result_keyboard(callback)
    await _rerun_from_session(callback, state, aspect_ratio=aspect, format_only=True)


async def _run_upscale(callback: CallbackQuery, *, scale: int, cost: int, alert_text: str) -> None:
    user = callback.from_user
    if user is None or callback.message is None:
        await callback.answer()
        return

    if not openrouter_images_configured(settings):
        await callback.answer(msg.TXT_GEN_JOB_FAILED, show_alert=True)
        return

    session = get_photo_edit_session(user.id, peer_id=callback.message.chat.id)
    if session is None or not session_has_result_image(session):
        await callback.answer(msg.TXT_PHOTO_REFINE_EXPIRED, show_alert=True)
        return

    row = await get_user_row(user.id)
    if int(row.crystals or 0) < cost:
        await callback.answer(alert_text, show_alert=True)
        return

    if not await try_consume_crystals(user.id, cost):
        await callback.answer(alert_text, show_alert=True)
        return

    image_url = await _resolve_session_upscale_source(session)
    if not image_url:
        from services.billing.crystals_balance import refund_crystals_to_buy

        await refund_crystals_to_buy(user.id, cost)
        await callback.answer(msg.TXT_PHOTO_REFINE_EXPIRED, show_alert=True)
        return

    await callback.answer(msg.TXT_UPSCALE_IN_PROGRESS)
    bot = deps.bot()
    chat_id = callback.message.chat.id

    try:
        async with chat_action_loop(bot, chat_id, "upload_document"):
            upscaled_url = await upscale_openrouter_image_url(
                settings,
                image_url,
                scale_value=scale,
            )
        await bot.send_document(
            chat_id,
            document=upscaled_url,
            caption=msg.TXT_UPSCALE_DONE,
        )
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
        from services.billing.crystals_balance import refund_crystals_to_buy

        await refund_crystals_to_buy(user.id, cost)
        logger.warning("upscale delivery failed uid=%s: %s", user.id, exc)
        await callback.message.answer(msg.TXT_GEN_JOB_FAILED)
    except Exception:
        from services.billing.crystals_balance import refund_crystals_to_buy

        await refund_crystals_to_buy(user.id, cost)
        logger.exception("upscale openrouter failed uid=%s scale=%s", user.id, scale)
        await callback.message.answer(msg.TXT_GEN_JOB_FAILED)


@router.callback_query(F.data == msg.CB_RESULT_UPSCALE_X2)
async def result_upscale_x2(callback: CallbackQuery) -> None:
    await _run_upscale(
        callback,
        scale=2,
        cost=UPSCALE_X2_COST,
        alert_text=msg.TXT_UPSCALE_X2_NEED_CRYSTAL,
    )


@router.callback_query(F.data == msg.CB_RESULT_UPSCALE_X4)
async def result_upscale_x4(callback: CallbackQuery) -> None:
    await _run_upscale(
        callback,
        scale=4,
        cost=UPSCALE_X4_COST,
        alert_text=msg.TXT_UPSCALE_X4_NEED_CRYSTAL,
    )
