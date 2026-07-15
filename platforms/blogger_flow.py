"""Inline-кнопки конструктора режима «Блогер» (хэштеги / Reels / обложка)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import settings
from content import messages as msg
from content.inline_keyboards import (
    get_blogger_adapt_keyboard,
    get_blogger_cover_face_keyboard,
    get_blogger_keyboard,
)
from platforms.telegram_states import UserFlow
from services import blogger_post_cache
from services.blogger_adaptation import (
    adapt_blogger_post_with_billing,
    adapt_platform_label,
    parse_adapt_target,
    prepare_adapted_telegram_html,
)
from services.blogger_cover import (
    deliver_blogger_cover_turn_result,
    handle_blogger_cover_callback,
    run_blogger_cover_turn,
)
from services.billing.blogger_pipeline import can_afford_blogger_adapt, can_afford_blogger_cover
from services.god_mode import billing_bypass
from services.repository import has_blogger_face_photo, set_blogger_face_file_id
from services.telegram_safe_text import prepare_telegram_html_text

logger = logging.getLogger(__name__)

router = Router(name="blogger_flow")


_BLOG_COVER_PREFIXES = (msg.CB_BLOGGER_COVER_PREFIX, msg.CB_BLOG_ART_PREFIX)


def _post_id_from_callback(data: str, prefix: str) -> str | None:
    if not data.startswith(prefix):
        return None
    post_id = data[len(prefix) :].strip()
    return post_id or None


def _post_id_from_cover_callback(data: str) -> str | None:
    for prefix in _BLOG_COVER_PREFIXES:
        post_id = _post_id_from_callback(data, prefix)
        if post_id:
            return post_id
    return None


def _parse_run_adapt(data: str) -> tuple[str, str] | None:
    prefix = msg.CB_BLOG_RUN_ADAPT_PREFIX
    if not data.startswith(prefix):
        return None
    rest = data[len(prefix) :]
    if ":" not in rest:
        return None
    post_id, platform = rest.rsplit(":", 1)
    post_id = post_id.strip()
    platform = platform.strip()
    if not post_id or not platform:
        return None
    return post_id, platform


async def _guard_blogger_post(callback: CallbackQuery, prefix: str) -> blogger_post_cache.BloggerPostDraft | None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return None
    post_id = _post_id_from_callback(callback.data or "", prefix)
    if not post_id:
        await callback.answer()
        return None

    user_id = callback.from_user.id
    draft = blogger_post_cache.get(post_id, user_id)
    if draft is None:
        draft = blogger_post_cache.get_by_message(
            callback.message.chat.id,
            callback.message.message_id,
            user_id,
        )
    if draft is None:
        await callback.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, show_alert=True)
        return None

    bound = blogger_post_cache.bind_telegram_message(
        draft.post_id,
        user_id,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
    )
    return bound or draft


def _resolve_draft_for_adapt(
    callback: CallbackQuery,
    user_id: int,
) -> blogger_post_cache.BloggerPostDraft | None:
    """Черновик для адаптации: привязка сообщения → последний пост пользователя."""
    if callback.message is None:
        return None
    draft = blogger_post_cache.get_by_message(
        callback.message.chat.id,
        callback.message.message_id,
        user_id,
    )
    if draft is None:
        draft = blogger_post_cache.get_last(user_id)
    return draft


async def _resolve_draft_for_adapt_guarded(callback: CallbackQuery) -> blogger_post_cache.BloggerPostDraft | None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return None

    user_id = callback.from_user.id
    draft = _resolve_draft_for_adapt(callback, user_id)
    if draft is None:
        await callback.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, show_alert=True)
        return None

    blogger_post_cache.bind_telegram_message(
        draft.post_id,
        user_id,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
    )
    return draft


async def _resolve_cover_draft(callback: CallbackQuery, post_id: str) -> blogger_post_cache.BloggerPostDraft | None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return None

    user_id = callback.from_user.id
    draft = blogger_post_cache.get(post_id, user_id)
    if draft is None:
        draft = blogger_post_cache.get_by_message(
            callback.message.chat.id,
            callback.message.message_id,
            user_id,
        )
    if draft is None:
        await callback.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, show_alert=True)
        return None

    blogger_post_cache.bind_telegram_message(
        draft.post_id,
        user_id,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
    )
    return draft


async def _start_blogger_cover_generation(
    callback: CallbackQuery,
    draft: blogger_post_cache.BloggerPostDraft,
    *,
    use_face: bool,
) -> None:
    await handle_blogger_cover_callback(settings, callback, draft, use_face=use_face)


@router.callback_query(F.data.startswith(msg.CB_ADAPT_TARGET_PREFIX))
async def cb_blogger_adapt_target(callback: CallbackQuery) -> None:
    """Запуск адаптации: только ``===ТЕЛО ПОСТА===`` → новое сообщение в чате."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    platform = parse_adapt_target(callback.data or "")
    if platform is None:
        await callback.answer()
        return

    draft = await _resolve_draft_for_adapt_guarded(callback)
    if draft is None:
        return

    source_body = draft.parsed.body
    if not source_body:
        await callback.answer(msg.TXT_BLOGGER_ADAPT_BODY_MISSING, show_alert=True)
        return

    user_id = callback.from_user.id
    if not billing_bypass(user_id) and not await can_afford_blogger_adapt(user_id):
        await callback.answer(msg.TXT_BLOGGER_ADAPT_INSUFFICIENT, show_alert=True)
        return

    await callback.answer(msg.TXT_BLOGGER_ADAPT_QUEUED)

    adapt_result = await adapt_blogger_post_with_billing(
        settings,
        source_body=source_body,
        platform=platform,
        user_id=user_id,
    )
    if adapt_result.error == "insufficient_crystals":
        await callback.message.answer(msg.TXT_BLOGGER_ADAPT_INSUFFICIENT, parse_mode=ParseMode.HTML)
        return
    if not adapt_result.ok or not adapt_result.content:
        await callback.message.answer(msg.TXT_BLOGGER_ADAPT_FAILED, parse_mode=ParseMode.HTML)
        return

    adapted = adapt_result.content

    body_html = prepare_adapted_telegram_html(adapted)
    platform_label = adapt_platform_label(platform)
    await callback.message.answer(
        msg.TXT_BLOGGER_ADAPT_RESULT.format(platform=platform_label, body=body_html),
        parse_mode=ParseMode.HTML,
    )
    logger.info(
        "blogger adapt done uid=%s post_id=%s platform=%s",
        draft.user_id,
        draft.post_id,
        platform,
    )


@router.callback_query(F.data.startswith(msg.CB_BLOG_RUN_ADAPT_PREFIX))
async def cb_blogger_run_adapt_legacy(callback: CallbackQuery) -> None:
    """Legacy ``blog_run_adapt:<post_id>:<platform>`` — перенаправление на новые цели."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    parsed = _parse_run_adapt(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    post_id, legacy_platform = parsed
    legacy_map = {"reels": "video", "twitter": "vk"}
    platform = legacy_map.get(legacy_platform, legacy_platform)
    if parse_adapt_target(f"{msg.CB_ADAPT_TARGET_PREFIX}{platform}") is None:
        await callback.answer()
        return

    draft = blogger_post_cache.get(post_id, callback.from_user.id)
    if draft is None:
        draft = _resolve_draft_for_adapt(callback, callback.from_user.id)
    if draft is None:
        await callback.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, show_alert=True)
        return

    blogger_post_cache.bind_telegram_message(
        draft.post_id,
        callback.from_user.id,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
    )

    callback.data = f"{msg.CB_ADAPT_TARGET_PREFIX}{platform}"
    await cb_blogger_adapt_target(callback)


def _message_html_text(callback: CallbackQuery) -> str | None:
    message = callback.message
    if message is None:
        return None
    html_text = getattr(message, "html_text", None)
    if html_text:
        return html_text
    return message.text


@router.callback_query(F.data.startswith(msg.CB_BLOG_HASH_PREFIX))
async def cb_blogger_hashtags(callback: CallbackQuery) -> None:
    """Кнопка «#️⃣ Подобрать хэштеги» — дописывает блок хэштегов к посту."""
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_HASH_PREFIX)
    if draft is None or callback.message is None:
        return

    if draft.hashtags_applied:
        await callback.answer(msg.TXT_BLOGGER_HASHTAGS_ADDED)
        return

    hashtags_block = draft.hashtags
    if not hashtags_block:
        await callback.answer(msg.TXT_BLOGGER_GENERATE_FIRST, show_alert=True)
        return

    current_text = _message_html_text(callback)
    if not current_text:
        await callback.answer(msg.TXT_BLOGGER_GENERATE_FIRST, show_alert=True)
        return

    hashtags_html = prepare_telegram_html_text(hashtags_block)
    updated_text = f"{current_text.rstrip()}\n\n{hashtags_html}"
    reply_markup = get_blogger_keyboard(draft.post_id, include_hashtags=False)

    try:
        await callback.message.edit_text(
            updated_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest:
        logger.warning(
            "blogger hashtags edit_text failed uid=%s post_id=%s",
            draft.user_id,
            draft.post_id,
            exc_info=True,
        )
        await callback.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, show_alert=True)
        return

    blogger_post_cache.mark_hashtags_applied(
        draft.post_id,
        draft.user_id,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        display_text=updated_text,
    )
    await callback.answer(msg.TXT_BLOGGER_HASHTAGS_ADDED)


@router.callback_query(F.data.startswith(msg.CB_BLOG_ADAPT_PREFIX))
async def cb_blogger_adapt_menu(callback: CallbackQuery) -> None:
    """Карусель выбора площадки для реформата поста."""
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_ADAPT_PREFIX)
    if draft is None or callback.message is None:
        return

    if not draft.parsed.body:
        await callback.answer(msg.TXT_BLOGGER_ADAPT_BODY_MISSING, show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_blogger_adapt_keyboard(draft.post_id),
        )
    except TelegramBadRequest:
        logger.warning(
            "blogger adapt menu edit_reply_markup failed uid=%s post_id=%s",
            draft.user_id,
            draft.post_id,
            exc_info=True,
        )
    await callback.answer()


@router.callback_query(F.data.startswith(msg.CB_BLOG_BACK_PREFIX))
async def cb_blogger_back_to_constructor(callback: CallbackQuery) -> None:
    """Возврат из подменю адаптации к основным кнопкам конструктора."""
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_BACK_PREFIX)
    if draft is None or callback.message is None:
        return

    reply_markup = get_blogger_keyboard(
        draft.post_id,
        include_hashtags=not draft.hashtags_applied,
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest:
        logger.warning(
            "blogger back edit_reply_markup failed uid=%s post_id=%s",
            draft.user_id,
            draft.post_id,
            exc_info=True,
        )
    await callback.answer()


@router.callback_query(
    F.data.startswith(msg.CB_BLOGGER_COVER_PREFIX) | F.data.startswith(msg.CB_BLOG_ART_PREFIX)
)
async def cb_blogger_cover_art(callback: CallbackQuery) -> None:
    """Кнопка «🎨 AI-обложка» — проверка фото лица → выбор или генерация Flux Schnell."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    post_id = _post_id_from_cover_callback(callback.data or "")
    if not post_id:
        await callback.answer()
        return

    draft = await _resolve_cover_draft(callback, post_id)
    if draft is None:
        return

    user_id = callback.from_user.id
    if await has_blogger_face_photo(user_id):
        await _start_blogger_cover_generation(callback, draft, use_face=True)
        return

    await callback.answer()
    await callback.message.answer(
        msg.TXT_BLOGGER_COVER_FACE_CHOICE,
        reply_markup=get_blogger_cover_face_keyboard(draft.post_id),
    )


@router.callback_query(F.data.startswith(msg.CB_BLOGGER_COVER_UPLOAD_FACE_PREFIX))
async def cb_blogger_cover_upload_face(callback: CallbackQuery, state: FSMContext) -> None:
    """«📸 Загрузить фото» — ожидание снимка лица для обложки с пользователем."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    post_id = _post_id_from_callback(callback.data or "", msg.CB_BLOGGER_COVER_UPLOAD_FACE_PREFIX)
    if not post_id:
        await callback.answer()
        return

    draft = await _resolve_cover_draft(callback, post_id)
    if draft is None:
        return

    await state.set_state(UserFlow.waiting_for_blogger_face_photo)
    await state.update_data(blogger_cover_post_id=draft.post_id)
    await callback.answer()
    await callback.message.answer(
        msg.TXT_BLOGGER_COVER_UPLOAD_FACE_HINT,
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith(msg.CB_BLOGGER_COVER_NO_FACE_PREFIX))
async def cb_blogger_cover_no_face(callback: CallbackQuery) -> None:
    """«🖼️ Создать без фото» — стандартная обложка по сюжету поста."""
    if callback.from_user is None:
        await callback.answer()
        return

    post_id = _post_id_from_callback(callback.data or "", msg.CB_BLOGGER_COVER_NO_FACE_PREFIX)
    if not post_id:
        await callback.answer()
        return

    draft = await _resolve_cover_draft(callback, post_id)
    if draft is None:
        return

    await _start_blogger_cover_generation(callback, draft, use_face=False)


@router.message(UserFlow.waiting_for_blogger_face_photo, F.photo)
async def blogger_face_photo_upload(message: Message, state: FSMContext) -> None:
    """Сохраняет фото лица в БД и сразу запускает генерацию обложки."""
    if message.from_user is None:
        return

    user_id = message.from_user.id
    data = await state.get_data()
    post_id = str(data.get("blogger_cover_post_id") or "").strip()
    await state.clear()

    file_id = message.photo[-1].file_id
    await set_blogger_face_file_id(user_id, file_id)

    if not post_id:
        await message.answer("✅ Фото лица сохранено. Нажмите «🎨 Создать AI-обложку» у поста.")
        return

    draft = blogger_post_cache.get(post_id, user_id)
    if draft is None:
        await message.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, parse_mode=ParseMode.HTML)
        return

    if not billing_bypass(user_id) and not await can_afford_blogger_cover(user_id):
        await message.answer(msg.TXT_BLOGGER_COVER_INSUFFICIENT, parse_mode=ParseMode.HTML)
        return

    await message.answer(msg.TXT_BLOGGER_COVER_FACE_SAVED)
    result = await run_blogger_cover_turn(
        settings,
        user_id=user_id,
        draft=draft,
        use_face=True,
        bot=message.bot,
    )
    await deliver_blogger_cover_turn_result(message, result, draft=draft)


@router.message(UserFlow.waiting_for_blogger_face_photo)
async def blogger_face_photo_need_photo(message: Message) -> None:
    await message.answer(msg.TXT_BLOGGER_COVER_UPLOAD_FACE_HINT, parse_mode=ParseMode.HTML)
