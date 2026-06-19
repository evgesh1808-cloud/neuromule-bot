"""Модуль гейминфикации отзывов NeuroMule 🐎⚡️.

Цикл:

1. Юзер жмёт ``✍️ Оставить отзыв (+5 ⚡)`` в ЛК → FSM ``waiting_for_review``.
2. Любой текст/фото/видео/документ ловится FSM-collector'ом → пересылка
   копии в админ-чат ``settings.reviews_admin_chat_id`` с инлайн-кнопками
   ``✅ Одобрить`` / ``❌ Отклонить``.
3. Атомарное начисление ``settings.review_energy_bonus`` ⚡ автору.
4. Дофамин-пуш с HTML-чеком.
5. Админ одобряет → отзыв летит в ``settings.gallery_channel_id`` с тегами
   ``#user_reviews #review_<id>``. Отклонение — мягкий пуш юзеру.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings as app_settings
from content import messages as msg
from platforms.telegram_states import UserFlow
from services import gallery_service, reviews_service

logger = logging.getLogger(__name__)

router = Router(name="reviews")


# ─── helpers ───────────────────────────────────────────────────────────────


def _admin_chat_configured() -> bool:
    return int(app_settings.reviews_admin_chat_id or 0) != 0


def _moderation_keyboard(review_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_REVIEW_APPROVE_BTN,
                    callback_data=f"{msg.CB_REVIEW_APPROVE_PREFIX}{review_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=msg.TXT_REVIEW_REJECT_BTN,
                    callback_data=f"{msg.CB_REVIEW_REJECT_PREFIX}{review_id}",
                )
            ],
        ]
    )


def _detect_review_kind(message: Message) -> tuple[str, str | None, str]:
    """Возвращает ``(kind, file_id, content)``.

    Контент = text || caption || пусто. File_id берётся из наибольшего размера
    для photo, чтобы прикрепить адекватное превью.
    """

    if message.photo:
        return "photo", message.photo[-1].file_id, (message.caption or "").strip()
    if message.video:
        return "video", message.video.file_id, (message.caption or "").strip()
    if message.document:
        return "document", message.document.file_id, (message.caption or "").strip()
    return "text", None, (message.text or "").strip()


async def _send_to_admin_chat(
    message: Message,
    *,
    review_id: int,
    kind: str,
    file_id: str | None,
    content: str,
    user_id: int,
) -> None:
    """Дублирует отзыв в чат-админку с inline-модерацией."""

    if not _admin_chat_configured():
        logger.info("reviews: admin chat not configured, skip moderation send")
        return

    chat_id = int(app_settings.reviews_admin_chat_id)
    header = msg.TXT_REVIEW_ADMIN_HEADER.format(user_id=user_id)
    kb = _moderation_keyboard(review_id)
    caption_full = header + ("\n\n" + content if content else "")

    bot = message.bot
    try:
        if kind == "photo" and file_id:
            await bot.send_photo(
                chat_id,
                photo=file_id,
                caption=caption_full[:1024],
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        elif kind == "video" and file_id:
            await bot.send_video(
                chat_id,
                video=file_id,
                caption=caption_full[:1024],
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        elif kind == "document" and file_id:
            await bot.send_document(
                chat_id,
                document=file_id,
                caption=caption_full[:1024],
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await bot.send_message(
                chat_id,
                caption_full,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
    except Exception:
        logger.exception("reviews: admin send failed review_id=%s", review_id)


# ─── entry: кнопка из ЛК ────────────────────────────────────────────────────


@router.callback_query(F.data == msg.CB_LEAVE_REVIEW)
async def cb_leave_review(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(UserFlow.waiting_for_review)
    try:
        await callback.message.answer(msg.TXT_REVIEW_ASK, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(msg.TXT_REVIEW_ASK)


# ─── collector: текст / фото / видео / документ ────────────────────────────


@router.message(UserFlow.waiting_for_review)
async def collect_review(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id

    kind, file_id, content = _detect_review_kind(message)
    if kind == "text" and not content:
        # Пустой ввод — продолжаем ждать осмысленный отзыв.
        await message.answer(msg.TXT_REVIEW_ASK, parse_mode=ParseMode.HTML)
        return

    review_id = await reviews_service.submit_review(
        user_id,
        kind=kind,  # type: ignore[arg-type]
        content=content,
        file_id=file_id,
    )
    await reviews_service.grant_review_bonus(
        user_id, amount=int(app_settings.review_energy_bonus)
    )
    await state.clear()

    await _send_to_admin_chat(
        message,
        review_id=review_id,
        kind=kind,
        file_id=file_id,
        content=content,
        user_id=user_id,
    )

    try:
        await message.answer(msg.TXT_REVIEW_THANKS, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await message.answer(msg.TXT_REVIEW_THANKS)


# ─── модерация (только из админ-чата) ──────────────────────────────────────


def _is_admin_caller(callback: CallbackQuery) -> bool:
    """Гард: модерация работает только из настроенного админ-чата."""

    if not _admin_chat_configured():
        return False
    if callback.message is None or callback.message.chat is None:
        return False
    return int(callback.message.chat.id) == int(app_settings.reviews_admin_chat_id)


async def _notify_author(bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
    except Exception:
        logger.info("reviews: notify author failed uid=%s", user_id, exc_info=True)


@router.callback_query(F.data.startswith(msg.CB_REVIEW_APPROVE_PREFIX))
async def cb_review_approve(callback: CallbackQuery) -> None:
    await callback.answer()
    if not _is_admin_caller(callback) or callback.message is None:
        return
    try:
        review_id = int((callback.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        return

    review = await reviews_service.get_review(review_id)
    if review is None:
        await callback.message.reply("⚠️ Отзыв уже удалён.")
        return

    await reviews_service.set_review_status(review_id, "approved")
    text_for_channel = review.get("content") or "(без текста)"
    posted = await gallery_service.post_review_to_channel(
        callback.message.bot,
        review_text=text_for_channel,
        review_id=review_id,
    )
    suffix = "✅ Опубликован в канале" if posted else "✅ Одобрен (канал недоступен)"
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.reply(suffix)
    await _notify_author(
        callback.message.bot,
        int(review["user_id"]),
        msg.TXT_REVIEW_APPROVED_NOTIFY,
    )


@router.callback_query(F.data.startswith(msg.CB_REVIEW_REJECT_PREFIX))
async def cb_review_reject(callback: CallbackQuery) -> None:
    await callback.answer()
    if not _is_admin_caller(callback) or callback.message is None:
        return
    try:
        review_id = int((callback.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        return

    review = await reviews_service.get_review(review_id)
    if review is None:
        await callback.message.reply("⚠️ Отзыв уже удалён.")
        return

    await reviews_service.set_review_status(review_id, "rejected")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.reply("❌ Отзыв отклонён.")
    await _notify_author(
        callback.message.bot,
        int(review["user_id"]),
        msg.TXT_REVIEW_REJECTED_NOTIFY,
    )


__all__ = ("router",)
