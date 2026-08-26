"""Отправка длинных ответов в Telegram несколькими сообщениями (лимит ~4096 символов)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import Settings
from content import messages as msg
from services.telegram_safe_text import (
    prepare_telegram_html_text,
    repair_telegram_html,
    sanitize_telegram_plain_text,
)

if TYPE_CHECKING:
    from aiogram.types import Message

logger = logging.getLogger(__name__)

# Жёсткий потолок одного Telegram-сообщения (с запасом под HTML-сущности).
_TELEGRAM_MSG_MAX = 4090


def split_telegram_text_chunks(text: str, chunk_size: int) -> list[str]:
    """Нарезает текст на куски ≤ ``chunk_size``, предпочитая границы абзацев/строк."""
    if not text:
        return []
    size = max(500, min(int(chunk_size), _TELEGRAM_MSG_MAX))
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            window = text[start:end]
            br = window.rfind("\n\n")
            if br < size // 3:
                br = window.rfind("\n")
            if br >= size // 3:
                end = start + br + (2 if window[br : br + 2] == "\n\n" else 1)
        chunk = text[start:end]
        if chunk:
            parts.append(chunk)
        start = end
    return parts or [text[:size]]


def _cap_html(text: str) -> str:
    capped = repair_telegram_html(text)
    if len(capped) > _TELEGRAM_MSG_MAX:
        return capped[: _TELEGRAM_MSG_MAX - 1] + "…"
    return capped


def _emergency_ascii_keyboard() -> InlineKeyboardMarkup:
    """3 ASCII chat_hint-кнопки — всегда валидный callback_data для Telegram."""
    from services.standard_suggested_replies import _EMERGENCY_ASCII_HINTS

    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"{msg.CB_CHAT_HINT_PREFIX}{label}",
            )
        ]
        for label in _EMERGENCY_ASCII_HINTS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def answer_free_standard_success(
    message: "Message",
    text: str,
    settings: Settings,
    *,
    labels: Sequence[str] | None = None,
    user_id: int | None = None,
    root_user_prompt: str = "",
) -> "Message":
    """Атомарная отправка FREE Standard: текст + кнопки (HintSession ``btn:``).

    Инвариант: Telegram НЕ получает SUCCESS-текст без ``reply_markup``.
    Паттерн: create_hint_session_persisted → send → bind_hint_session_message_persisted.
    Legacy ``chat_hint`` — только если session-клавиатура не собралась.
    """
    _ = settings
    from services.standard_suggested_replies import (
        bind_hint_session_message_persisted,
        build_free_hint_keyboard,
        build_hint_keyboard,
        create_hint_session_persisted,
        ensure_free_hint_labels,
    )

    body = text or ""
    action_uuid: str | None = None
    kb = None
    owner = int(user_id) if user_id is not None else (
        int(message.from_user.id) if message.from_user else 0
    )
    try:
        clean = ensure_free_hint_labels(labels, body=body)
        if owner and clean:
            action_uuid = await create_hint_session_persisted(
                owner,
                body=body,
                labels=clean,
                root_user_prompt=root_user_prompt or "",
                message_id=None,
            )
            kb = build_hint_keyboard(action_uuid, clean)
    except Exception:
        logger.exception("free_standard_send: HintSession keyboard failed")
        action_uuid = None
        kb = None
    if kb is None:
        # Soft legacy fallback — кнопки всё равно уходят.
        kb = build_free_hint_keyboard(labels, body=body)
        action_uuid = None

    html = _cap_html(prepare_telegram_html_text(body, max_len=None))
    plain = sanitize_telegram_plain_text(html)

    async def _bind(sent: "Message") -> "Message":
        if action_uuid:
            await bind_hint_session_message_persisted(action_uuid, int(sent.message_id))
        return sent

    try:
        sent = await message.answer(html, parse_mode=ParseMode.HTML, reply_markup=kb)
        logger.info(
            "free_standard_send: ok html+kb chat=%s buttons=%s hint=%s",
            message.chat.id,
            len(kb.inline_keyboard),
            bool(action_uuid),
        )
        return await _bind(sent)
    except TelegramBadRequest:
        logger.warning(
            "free_standard_send: HTML+kb rejected — retry plain+kb",
            exc_info=True,
        )

    try:
        sent = await message.answer(plain, reply_markup=kb)
        logger.info(
            "free_standard_send: ok plain+kb chat=%s buttons=%s hint=%s",
            message.chat.id,
            len(kb.inline_keyboard),
            bool(action_uuid),
        )
        return await _bind(sent)
    except TelegramBadRequest:
        logger.warning(
            "free_standard_send: plain+kb rejected — retry ASCII emergency kb",
            exc_info=True,
        )

    emergency = _emergency_ascii_keyboard()
    sent = await message.answer(plain, reply_markup=emergency)
    logger.warning(
        "free_standard_send: used ASCII emergency kb chat=%s",
        message.chat.id,
    )
    # Emergency chat_hint — без bind HintSession.
    return sent


async def answer_chat_text(
    message: "Message",
    text: str,
    settings: Settings,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> "Message | None":
    """
    Одно сообщение, если текст короткий; иначе нарезка по ``chat_reply_chunk_size``.

    Если передан ``reply_markup`` — текст без клавиатуры НЕ отправляется
    (иначе кнопки «отваливаются» после мягкого fallback).
    """
    safe = prepare_telegram_html_text(text, max_len=None)
    last_sent: Message | None = None

    async def _answer(part: str, *, markup: InlineKeyboardMarkup | None = None) -> "Message":
        capped = _cap_html(part)
        plain = sanitize_telegram_plain_text(capped)
        try:
            return await message.answer(
                capped, parse_mode=ParseMode.HTML, reply_markup=markup
            )
        except TelegramBadRequest:
            logger.debug("answer_chat_text: HTML send failed, retry plain", exc_info=True)
        try:
            return await message.answer(plain, reply_markup=markup)
        except TelegramBadRequest:
            if markup is None:
                return await message.answer(plain)
            # Markup обязателен: не коммитим «голый» текст.
            logger.error(
                "answer_chat_text: send with required markup failed — re-raising",
                exc_info=True,
            )
            raise

    if len(safe) <= settings.chat_chunk_reply_threshold:
        return await _answer(safe, markup=reply_markup)
    chunk = max(500, min(settings.chat_reply_chunk_size, _TELEGRAM_MSG_MAX))
    parts = split_telegram_text_chunks(safe, chunk)
    for idx, part in enumerate(parts):
        markup = reply_markup if idx == len(parts) - 1 else None
        last_sent = await _answer(part, markup=markup)
    return last_sent


async def send_chat_html(
    bot,
    chat_id: int,
    text: str,
    settings: Settings,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Длинный HTML в чат ``chat_id`` — с нарезкой и plain-fallback (HD-разделы, поздравления)."""
    safe = prepare_telegram_html_text(text, max_len=None)
    threshold = settings.chat_chunk_reply_threshold
    chunk_size = max(500, min(settings.chat_reply_chunk_size, _TELEGRAM_MSG_MAX))

    async def _send(part: str, *, markup: InlineKeyboardMarkup | None) -> None:
        capped = _cap_html(part)
        plain = sanitize_telegram_plain_text(capped)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=capped,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            logger.debug("send_chat_html: HTML send failed, retry plain", exc_info=True)
        try:
            await bot.send_message(chat_id=chat_id, text=plain, reply_markup=markup)
        except TelegramBadRequest:
            if markup is None:
                await bot.send_message(chat_id=chat_id, text=plain)
                return
            logger.error(
                "send_chat_html: send with required markup failed chat_id=%s",
                chat_id,
                exc_info=True,
            )
            raise

    if len(safe) <= threshold:
        await _send(safe, markup=reply_markup)
        return
    parts = split_telegram_text_chunks(safe, chunk_size)
    for idx, part in enumerate(parts):
        markup = reply_markup if idx == len(parts) - 1 else None
        await _send(part, markup=markup)
