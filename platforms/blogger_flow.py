"""Inline-кнопки конструктора режима «Блогер» (хэштеги / Reels / обложка)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from config import settings
from content import messages as msg
from content.inline_keyboards import get_blogger_keyboard
from services import payments_catalog as paycat
from services import blogger_post_cache
from services.telegram_safe_text import prepare_telegram_html_text

logger = logging.getLogger(__name__)

router = Router(name="blogger_flow")

_BLOG_COVER_MODEL_ID = "imagen4"


def _post_id_from_callback(data: str, prefix: str) -> str | None:
    if not data.startswith(prefix):
        return None
    post_id = data[len(prefix) :].strip()
    return post_id or None


async def _guard_blogger_post(callback: CallbackQuery, prefix: str) -> blogger_post_cache.BloggerPostDraft | None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return None
    post_id = _post_id_from_callback(callback.data or "", prefix)
    if not post_id:
        await callback.answer()
        return None
    draft = blogger_post_cache.get(post_id, callback.from_user.id)
    if draft is None:
        await callback.answer(msg.TXT_BLOGGER_POST_NOT_FOUND, show_alert=True)
        return None
    return draft


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

    blogger_post_cache.mark_hashtags_applied(draft.post_id, draft.user_id)
    await callback.answer(msg.TXT_BLOGGER_HASHTAGS_ADDED)


@router.callback_query(F.data.startswith(msg.CB_BLOG_ADAPT_PREFIX))
async def cb_blogger_adapt_reels(callback: CallbackQuery) -> None:
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_ADAPT_PREFIX)
    if draft is None:
        return
    logger.info("blogger adapt upsell uid=%s post_id=%s", draft.user_id, draft.post_id)
    await callback.answer(msg.TXT_BLOGGER_UPSELL_SOON, show_alert=True)


@router.callback_query(F.data.startswith(msg.CB_BLOG_ART_PREFIX))
async def cb_blogger_cover_art(callback: CallbackQuery) -> None:
    """Кнопка «🎨 Создать AI-обложку» — берёт промпт из кэша и ставит генерацию."""
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_ART_PREFIX)
    if draft is None or callback.message is None:
        return

    clean_prompt = draft.image_prompt
    if not clean_prompt:
        await callback.answer(msg.TXT_BLOGGER_IMAGE_PROMPT_NOT_FOUND, show_alert=True)
        return

    await callback.answer(msg.TXT_BLOGGER_ART_QUEUED, show_alert=True)

    label = next(
        (lbl for lbl, mid in msg.IMAGE_MODELS if mid == _BLOG_COVER_MODEL_ID),
        "Imagen 4",
    )

    from services.use_cases.photo_generation_turn import (
        PhotoGenOutcome,
        run_photo_generation_turn,
    )

    result = await run_photo_generation_turn(
        settings,
        callback.message.bot,
        callback.message.chat.id,
        callback.from_user.id,
        _BLOG_COVER_MODEL_ID,
        label,
        clean_prompt,
    )

    if result.outcome is PhotoGenOutcome.SUCCESS:
        logger.info("blogger cover queued uid=%s post_id=%s", draft.user_id, draft.post_id)
        return

    if result.outcome is PhotoGenOutcome.INSUFFICIENT_BALANCE:
        await callback.message.answer(
            msg.TXT_CHAT_ZERO_BALANCE_PREMIUM,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    if result.outcome is PhotoGenOutcome.DAILY_LIMIT_EXCEEDED:
        await callback.message.answer(msg.TXT_CHAT_DAILY_LIMIT, parse_mode=ParseMode.HTML)
        return

    if result.outcome is PhotoGenOutcome.FREE_IMAGE_MODEL_BLOCKED:
        await callback.message.answer(msg.TXT_FREE_IMAGE_MODEL_BLOCKED, parse_mode=ParseMode.HTML)
        return

    safe_prompt = clean_prompt.replace("<", "&lt;").replace(">", "&gt;")
    await callback.message.answer(
        msg.TXT_BLOGGER_ART_PROMPT_SENT.format(prompt=safe_prompt),
        parse_mode=ParseMode.HTML,
    )
