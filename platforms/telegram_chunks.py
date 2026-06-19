"""Отправка длинных ответов в Telegram несколькими сообщениями (лимит ~4096 символов)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from config import Settings
from services.telegram_safe_text import prepare_telegram_html_text, sanitize_telegram_plain_text

if TYPE_CHECKING:
    from aiogram.types import Message


async def answer_chat_text(message: "Message", text: str, settings: Settings) -> None:
    """
    Одно сообщение, если текст короткий; иначе нарезка по ``chat_reply_chunk_size``.

    Порог «начинать нарезку» — ``chat_chunk_reply_threshold``.
    """
    safe = prepare_telegram_html_text(text)

    async def _answer(part: str) -> None:
        try:
            await message.answer(part, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await message.answer(sanitize_telegram_plain_text(part))

    if len(safe) <= settings.chat_chunk_reply_threshold:
        await _answer(safe)
        return
    chunk = max(500, settings.chat_reply_chunk_size)
    for i in range(0, len(safe), chunk):
        await _answer(safe[i : i + chunk])
