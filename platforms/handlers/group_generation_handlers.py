"""Групповое фото (до 10 лиц) — GPTron-style сборщик + явная генерация."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import settings
from content import messages as msg
from content.inline_keyboards import group_photo_collector_keyboard
from platforms.handlers import deps
from platforms.handlers.generation_fsm import _photo_reference_from_message
from platforms.telegram_keyboards import invite_limit_keyboard, photo_tools_menu
from platforms.telegram_states import GroupGenerationStates
from platforms.telegram_utils import try_dispatch_reply_nav_button
from services import payments_catalog as paycat
from services.generation_jobs import fire_photo_job
from services.photo_aspect_ratio import DEFAULT_PHOTO_ASPECT_RATIO
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn

logger = logging.getLogger(__name__)

router = Router(name="group_generation")

GROUP_PHOTO_MODEL_ID = "nano_banana_pro"
GROUP_PHOTO_MODEL_LABEL = "Nano Banana Pro"
MAX_GROUP_REFS = 10
MIN_GROUP_REFS = 2


def _can_generate(*, refs_count: int, group_prompt: str) -> bool:
    return refs_count >= MIN_GROUP_REFS and bool((group_prompt or "").strip())


def _extract_file_ids_from_messages(messages: list[Message]) -> list[str]:
    ordered = sorted(messages, key=lambda item: item.message_id or 0)
    file_ids: list[str] = []
    for item in ordered:
        file_id, _ = _photo_reference_from_message(item)
        if file_id:
            file_ids.append(file_id)
    return file_ids


def _album_caption(album_messages: list[Message]) -> str:
    for item in sorted(album_messages, key=lambda m: m.message_id or 0):
        caption = (item.caption or "").strip()
        if caption:
            return caption
    return ""


async def _render_status_card(
    target: Message,
    state: FSMContext,
    *,
    edit_message_id: int | None = None,
) -> int:
    data = await state.get_data()
    refs = list(data.get("group_refs") or [])
    group_prompt = str(data.get("group_prompt") or "")
    text = msg.format_group_photo_status_html(
        refs_count=len(refs),
        group_prompt=group_prompt,
    )
    keyboard = group_photo_collector_keyboard(
        can_generate=_can_generate(refs_count=len(refs), group_prompt=group_prompt),
    )
    if edit_message_id:
        try:
            await deps.bot().edit_message_text(
                text=text,
                chat_id=target.chat.id,
                message_id=edit_message_id,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            await state.update_data(group_status_message_id=edit_message_id)
            return edit_message_id
        except TelegramBadRequest:
            logger.debug("group photo: status edit failed, sending new card", exc_info=True)

    sent = await target.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await state.update_data(group_status_message_id=sent.message_id)
    return sent.message_id


async def _append_group_refs(
    message: Message,
    state: FSMContext,
    new_file_ids: list[str],
    *,
    caption: str = "",
) -> None:
    if not new_file_ids:
        return

    data = await state.get_data()
    refs: list[str] = list(data.get("group_refs") or [])
    seen = set(refs)
    truncated = False
    for file_id in new_file_ids:
        if file_id in seen:
            continue
        if len(refs) >= MAX_GROUP_REFS:
            truncated = True
            break
        refs.append(file_id)
        seen.add(file_id)

    updates: dict[str, object] = {"group_refs": refs}
    if caption and not str(data.get("group_prompt") or "").strip():
        updates["group_prompt"] = caption

    await state.update_data(**updates)

    if truncated:
        await message.answer(msg.TXT_GROUP_PHOTO_TOO_MANY, parse_mode=ParseMode.HTML)

    status_id = data.get("group_status_message_id")
    await _render_status_card(
        message,
        state,
        edit_message_id=int(status_id) if status_id else None,
    )


@router.callback_query(F.data == msg.CB_GROUP_PHOTO_START)
async def group_photo_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return

    await state.clear()
    await state.set_state(GroupGenerationStates.WAIT_ALBUM_OR_PHOTOS)
    await state.update_data(
        group_refs=[],
        group_prompt="",
        group_status_message_id=None,
    )
    await callback.message.answer(msg.TXT_GROUP_PHOTO_WELCOME, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == msg.CB_GROUP_PHOTO_PROMPT)
async def group_photo_prompt_request(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.set_state(GroupGenerationStates.WAIT_GROUP_PROMPT)
    await callback.message.answer(msg.TXT_GROUP_PHOTO_PROMPT_WAIT, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == msg.CB_GROUP_PHOTO_CLEAR)
async def group_photo_clear(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.update_data(group_refs=[])
    await state.set_state(GroupGenerationStates.WAIT_ALBUM_OR_PHOTOS)
    await _render_status_card(callback.message, state)
    await callback.message.answer(msg.TXT_GROUP_PHOTO_CLEARED, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == msg.CB_GROUP_PHOTO_CANCEL)
async def group_photo_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.clear()
    await callback.message.answer(
        msg.TXT_GROUP_PHOTO_CANCELLED,
        reply_markup=photo_tools_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "grp_photo_gen_locked")
async def group_photo_generate_locked(callback: CallbackQuery) -> None:
    await callback.answer(
        "Нужно минимум 2 фото и текстовый промпт.",
        show_alert=True,
    )


@router.callback_query(F.data == msg.CB_GROUP_PHOTO_GENERATE)
async def group_photo_generate(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None or callback.from_user is None:
        await callback.answer()
        return

    data = await state.get_data()
    refs = list(data.get("group_refs") or [])
    group_prompt = str(data.get("group_prompt") or "").strip()

    if len(refs) < MIN_GROUP_REFS:
        await callback.answer(msg.TXT_GROUP_PHOTO_NEED_MORE, show_alert=True)
        return
    if not group_prompt:
        await callback.answer(msg.TXT_GROUP_PHOTO_NEED_PROMPT, show_alert=True)
        return

    await callback.answer("🚀 Запускаю генерацию…")
    await _dispatch_group_generation(
        callback.message,
        state,
        user_id=callback.from_user.id,
        refs=refs,
        group_prompt=group_prompt,
    )


@router.message(GroupGenerationStates.WAIT_ALBUM_OR_PHOTOS, F.photo | F.document)
async def group_photo_collect(
    message: Message,
    state: FSMContext,
    album_messages: list[Message] | None = None,
) -> None:
    if await try_dispatch_reply_nav_button(message, state):
        return

    if album_messages and len(album_messages) > 1:
        file_ids = _extract_file_ids_from_messages(album_messages)
        caption = _album_caption(album_messages)
    else:
        file_id, caption = _photo_reference_from_message(message)
        file_ids = [file_id] if file_id else []

    if not file_ids:
        await message.answer("Отправьте фото участников (JPEG/PNG).", parse_mode=ParseMode.HTML)
        return

    await _append_group_refs(message, state, file_ids, caption=caption)


@router.message(GroupGenerationStates.WAIT_GROUP_PROMPT, F.text)
async def group_photo_prompt_text(message: Message, state: FSMContext) -> None:
    if await try_dispatch_reply_nav_button(message, state):
        return

    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer(msg.TXT_GROUP_PHOTO_PROMPT_WAIT, parse_mode=ParseMode.HTML)
        return

    await state.update_data(group_prompt=prompt)
    await state.set_state(GroupGenerationStates.WAIT_ALBUM_OR_PHOTOS)

    data = await state.get_data()
    status_id = data.get("group_status_message_id")
    await _render_status_card(
        message,
        state,
        edit_message_id=int(status_id) if status_id else None,
    )


@router.message(GroupGenerationStates.WAIT_ALBUM_OR_PHOTOS, F.text)
async def group_photo_album_prompt_text(message: Message, state: FSMContext) -> None:
    """Текст в режиме сбора — трактуем как промпт (если альбом ещё собирается — ждём)."""
    if await try_dispatch_reply_nav_button(message, state):
        return

    if message.from_user is not None:
        from platforms.media_group_middleware import album_collection_pending

        if album_collection_pending(message.from_user.id):
            await asyncio.sleep(1.15)

    prompt = (message.text or "").strip()
    if not prompt:
        return

    await state.update_data(group_prompt=prompt)
    data = await state.get_data()
    status_id = data.get("group_status_message_id")
    await _render_status_card(
        message,
        state,
        edit_message_id=int(status_id) if status_id else None,
    )


async def _dispatch_group_generation(
    message: Message,
    state: FSMContext,
    *,
    user_id: int,
    refs: list[str],
    group_prompt: str,
) -> None:
    from platforms.telegram_throttling import clear_photo_flow, mark_photo_flow
    from services.photo_gen_status import send_photo_gen_status_message

    chat_id = message.chat.id
    mark_photo_flow(user_id)

    status_msg = await send_photo_gen_status_message(
        deps.bot(),
        chat_id,
        model_label=GROUP_PHOTO_MODEL_LABEL,
        aspect_ratio=DEFAULT_PHOTO_ASPECT_RATIO,
        model_id=GROUP_PHOTO_MODEL_ID,
    )

    try:
        pr = await run_photo_generation_turn(
            settings,
            deps.bot(),
            chat_id,
            user_id,
            GROUP_PHOTO_MODEL_ID,
            GROUP_PHOTO_MODEL_LABEL,
            group_prompt,
            aspect_ratio=DEFAULT_PHOTO_ASPECT_RATIO,
            group_multi_ref=True,
            group_ref_file_ids=refs,
        )
    except ValueError as exc:
        logger.warning("group photo: invalid refs uid=%s: %s", user_id, exc)
        await message.answer(msg.TXT_GROUP_PHOTO_API_FAILED.format(refs_count=len(refs)), parse_mode=ParseMode.HTML)
        clear_photo_flow(user_id)
        return
    except Exception:
        logger.exception("group photo: billing/enqueue failed uid=%s", user_id)
        await message.answer(msg.TXT_GEN_JOB_FAILED, parse_mode=ParseMode.HTML)
        clear_photo_flow(user_id)
        return

    if pr.outcome is PhotoGenOutcome.NEED_PROMPT:
        await message.answer(msg.TXT_GROUP_PHOTO_NEED_PROMPT, parse_mode=ParseMode.HTML)
        clear_photo_flow(user_id)
        return
    if pr.outcome is PhotoGenOutcome.INSUFFICIENT_BALANCE:
        try:
            await status_msg.delete()
        except TelegramBadRequest:
            pass
        await message.answer(
            msg.TXT_INSUFFICIENT_BALANCE,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        clear_photo_flow(user_id)
        return
    if pr.outcome is PhotoGenOutcome.DAILY_LIMIT_EXCEEDED:
        try:
            await status_msg.delete()
        except TelegramBadRequest:
            pass
        await message.answer(
            msg.TXT_PHOTO_DAILY_LIMIT.format(limit=settings.free_daily_photo_limit),
            reply_markup=invite_limit_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        clear_photo_flow(user_id)
        return
    if pr.outcome in (
        PhotoGenOutcome.GLOBAL_FREE_IMAGE_CAP,
        PhotoGenOutcome.FREE_IMAGE_MODEL_BLOCKED,
    ):
        try:
            await status_msg.delete()
        except TelegramBadRequest:
            pass
        await message.answer(msg.TXT_FREE_IMAGE_MODEL_BLOCKED, parse_mode=ParseMode.HTML)
        clear_photo_flow(user_id)
        return

    eq = pr.enqueue
    if eq is None:
        await message.answer(msg.TXT_GEN_JOB_FAILED, parse_mode=ParseMode.HTML)
        clear_photo_flow(user_id)
        return

    try:
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
            aspect_ratio=eq.aspect_ratio,
            status_message_id=status_msg.message_id,
            group_multi_ref=True,
            group_ref_file_ids=eq.group_ref_file_ids,
        )
    except Exception:
        logger.exception("group photo: fire_photo_job failed uid=%s", user_id)
        if eq.billing_charge_id:
            try:
                from services.billing import refund_charge

                await refund_charge(eq.billing_charge_id)
            except Exception:
                logger.exception("group photo: refund failed charge=%s", eq.billing_charge_id)
        await message.answer(
            msg.TXT_GROUP_PHOTO_API_FAILED.format(refs_count=len(refs)),
            parse_mode=ParseMode.HTML,
        )
        clear_photo_flow(user_id)
        return

    await state.set_state(GroupGenerationStates.WAIT_ALBUM_OR_PHOTOS)
    mark_photo_flow(user_id)
