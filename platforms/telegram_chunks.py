"""Отправка длинных ответов в Telegram несколькими сообщениями (лимит ~4096 символов)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from config import Settings
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


async def answer_chat_text(
    message: "Message",
    text: str,
    settings: Settings,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> "Message | None":
    """
    Одно сообщение, если текст короткий; иначе нарезка по ``chat_reply_chunk_size``.

    Порог «начинать нарезку» — ``chat_chunk_reply_threshold``.
    Сначала готовим HTML без обрезки, затем режем — иначе ответы >4090 символов
    обрывались на «…» ещё до chunking.

    Если Telegram отвергает HTML+клавиатуру — текст уходит без markup, затем
    клавиатура довешивается через ``edit_reply_markup`` или отдельным сообщением.
    """
    safe = prepare_telegram_html_text(text, max_len=None)
    last_sent: Message | None = None

    async def _attach_markup(sent: "Message", markup: InlineKeyboardMarkup) -> None:
        try:
            await sent.edit_reply_markup(reply_markup=markup)
            return
        except TelegramBadRequest:
            logger.warning(
                "answer_chat_text: edit_reply_markup failed — fallback mini-message",
                exc_info=True,
            )
        try:
            await message.answer("👇", reply_markup=markup)
        except Exception:
            logger.exception("answer_chat_text: markup fallback answer failed")

    async def _answer(part: str, *, markup: InlineKeyboardMarkup | None = None) -> "Message":
        capped = repair_telegram_html(part)
        if len(capped) > _TELEGRAM_MSG_MAX:
            capped = capped[: _TELEGRAM_MSG_MAX - 1] + "…"
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
            logger.warning(
                "answer_chat_text: send with markup failed — text then attach",
                exc_info=True,
            )
            sent = await message.answer(plain)
            if markup is not None:
                await _attach_markup(sent, markup)
            return sent

    if len(safe) <= settings.chat_chunk_reply_threshold:
        return await _answer(safe, markup=reply_markup)
    chunk = max(500, min(settings.chat_reply_chunk_size, _TELEGRAM_MSG_MAX))
    parts = split_telegram_text_chunks(safe, chunk)
    for idx, part in enumerate(parts):
        markup = reply_markup if idx == len(parts) - 1 else None
        last_sent = await _answer(part, markup=markup)
    return last_sent
