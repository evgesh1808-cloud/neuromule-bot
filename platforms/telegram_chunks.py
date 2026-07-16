"""Отправка длинных ответов в Telegram несколькими сообщениями (лимит ~4096 символов)."""

from __future__ import annotations

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
) -> None:
    """
    Одно сообщение, если текст короткий; иначе нарезка по ``chat_reply_chunk_size``.

    Порог «начинать нарезку» — ``chat_chunk_reply_threshold``.
    Сначала готовим HTML без обрезки, затем режем — иначе ответы >4090 символов
    обрывались на «…» ещё до chunking.
    """
    safe = prepare_telegram_html_text(text, max_len=None)

    async def _answer(part: str, *, markup: InlineKeyboardMarkup | None = None) -> None:
        capped = repair_telegram_html(part)
        if len(capped) > _TELEGRAM_MSG_MAX:
            capped = capped[: _TELEGRAM_MSG_MAX - 1] + "…"
        try:
            await message.answer(capped, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramBadRequest:
            await message.answer(sanitize_telegram_plain_text(capped), reply_markup=markup)

    if len(safe) <= settings.chat_chunk_reply_threshold:
        await _answer(safe, markup=reply_markup)
        return
    chunk = max(500, min(settings.chat_reply_chunk_size, _TELEGRAM_MSG_MAX))
    parts = split_telegram_text_chunks(safe, chunk)
    for idx, part in enumerate(parts):
        markup = reply_markup if idx == len(parts) - 1 else None
        await _answer(part, markup=markup)
