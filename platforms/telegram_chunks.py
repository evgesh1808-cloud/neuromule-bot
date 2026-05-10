"""Отправка длинных ответов в Telegram несколькими сообщениями (лимит ~4096 символов)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from aiogram.types import Message


async def answer_chat_text(message: "Message", text: str, settings: Settings) -> None:
    """
    Одно сообщение, если текст короткий; иначе нарезка по ``chat_reply_chunk_size``.

    Порог «начинать нарезку» — ``chat_chunk_reply_threshold``.
    """
    if len(text) <= settings.chat_chunk_reply_threshold:
        await message.answer(text)
        return
    chunk = max(500, settings.chat_reply_chunk_size)
    for i in range(0, len(text), chunk):
        await message.answer(text[i : i + chunk])
