"""Отправка длинных ответов в Telegram несколькими сообщениями (лимит ~4096 символов)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from config import Settings
from services.telegram_safe_text import prepare_telegram_html_text, sanitize_telegram_plain_text

if TYPE_CHECKING:
    from aiogram.types import Message


async def answer_chat_text(
    message: "Message",
    text: str,
    settings: Settings,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Одно сообщение, если текст короткий; иначе нарезка по ``chat_reply_chunk_size``.

    Порог «начинать нарезку» — ``chat_chunk_reply_threshold``.
    """
    safe = prepare_telegram_html_text(text)

    async def _answer(part: str, *, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await message.answer(part, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramBadRequest:
            await message.answer(sanitize_telegram_plain_text(part), reply_markup=markup)

    if len(safe) <= settings.chat_chunk_reply_threshold:
        await _answer(safe, markup=reply_markup)
        return
    chunk = max(500, settings.chat_reply_chunk_size)
    parts = [safe[i : i + chunk] for i in range(0, len(safe), chunk)]
    for idx, part in enumerate(parts):
        markup = reply_markup if idx == len(parts) - 1 else None
        await _answer(part, markup=markup)
