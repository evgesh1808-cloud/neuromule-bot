"""Inline-кнопки конструктора режима «Блогер» (хэштеги / Reels / обложка)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from content import messages as msg
from services import blogger_post_cache

logger = logging.getLogger(__name__)

router = Router(name="blogger_flow")


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


@router.callback_query(F.data.startswith(msg.CB_BLOG_HASH_PREFIX))
async def cb_blogger_hashtags(callback: CallbackQuery) -> None:
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_HASH_PREFIX)
    if draft is None:
        return
    logger.info("blogger hash upsell uid=%s post_id=%s", draft.user_id, draft.post_id)
    await callback.answer(msg.TXT_BLOGGER_UPSELL_SOON, show_alert=True)


@router.callback_query(F.data.startswith(msg.CB_BLOG_ADAPT_PREFIX))
async def cb_blogger_adapt_reels(callback: CallbackQuery) -> None:
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_ADAPT_PREFIX)
    if draft is None:
        return
    logger.info("blogger adapt upsell uid=%s post_id=%s", draft.user_id, draft.post_id)
    await callback.answer(msg.TXT_BLOGGER_UPSELL_SOON, show_alert=True)


@router.callback_query(F.data.startswith(msg.CB_BLOG_ART_PREFIX))
async def cb_blogger_cover_art(callback: CallbackQuery) -> None:
    draft = await _guard_blogger_post(callback, msg.CB_BLOG_ART_PREFIX)
    if draft is None:
        return
    logger.info("blogger art upsell uid=%s post_id=%s", draft.user_id, draft.post_id)
    await callback.answer(msg.TXT_BLOGGER_UPSELL_SOON, show_alert=True)
