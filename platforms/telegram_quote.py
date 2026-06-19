"""Извлечение цитаты / Reply на сообщение бота для нейротекста."""

from __future__ import annotations

import re

from aiogram import F
from aiogram.types import Message

# Reply на сообщение этого бота (обычный «Ответить» или Reply + TextQuote).
REPLY_TO_BOT_FILTER = F.reply_to_message & (F.reply_to_message.from_user.id == F.bot.id)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text).strip()


def _utf16_offset_to_index(text: str, utf16_offset: int) -> int:
    """Индекс символа Python по смещению в UTF-16 code units (Bot API TextQuote.position)."""
    if utf16_offset <= 0:
        return 0
    units = 0
    for index, char in enumerate(text):
        if units >= utf16_offset:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def is_reply_to_bot_message(message: Message) -> bool:
    """Обычный Reply на сообщение бота (с TextQuote или без)."""
    replied = message.reply_to_message
    if replied is None:
        return False
    author = replied.from_user
    if not author:
        return False
    bot = message.bot
    if bot is not None:
        bot_id = getattr(bot, "id", None)
        if isinstance(bot_id, int):
            return author.id == bot_id
    return bool(author.is_bot)


def is_quote_reply_to_bot(message: Message) -> bool:
    """Reply + TextQuote на сообщение бота (нейротекст / чат)."""
    return message.quote is not None and is_reply_to_bot_message(message)


def _replied_bot_plain_text(message: Message) -> str | None:
    """Полный текст/caption сообщения бота, на которое отвечают."""
    replied = message.reply_to_message
    if replied is None:
        return None
    full = (replied.text or replied.caption or "").strip()
    if not full:
        return None
    plain = _strip_html(full)
    return plain or None


def extract_quoted_text(message: Message) -> str | None:
    """
    Выделенный фрагмент ответа бота: ``message.quote`` или срез по ``position``.

    Вызывать после ``is_quote_reply_to_bot`` или при наличии ``reply_to_message`` + ``quote``.
    """
    quote = message.quote
    if quote is None:
        return None

    direct = (quote.text or "").strip()
    if direct:
        return _strip_html(direct)

    replied = message.reply_to_message
    if replied is None:
        return None

    full = (replied.text or replied.caption or "").strip()
    if not full:
        return None

    plain = _strip_html(full)
    start = _utf16_offset_to_index(plain, int(quote.position or 0))
    return plain[start:].strip() or None


def build_quoted_user_prompt(user_text: str, quoted_text: str | None) -> str:
    """
    Пакет для OpenRouter: цитата в ``<blockquote>`` (см. ``chat_prompt``) + вопрос пользователя.

    В БД истории сохраняется только ``user_text`` — расширенный текст подставляется в payload
    в ``run_chat_turn`` через ``dialog_user_text``.
    """
    if not quoted_text:
        return user_text
    comment = (user_text or "").strip() or "Прокомментируй выделенный фрагмент."
    return (
        "Пользователь комментирует твою фразу:\n"
        f"<blockquote>{quoted_text}</blockquote>\n\n"
        f"Его вопрос: {comment}"
    )


def resolve_neurotext_quote_input(message: Message) -> tuple[str | None, str]:
    """
    Из Reply на бота: (контекст прошлой реплики | None, комментарий пользователя).

    * Reply + TextQuote — выделенный фрагмент (``extract_quoted_text``).
    * Обычный Reply без Quote — полный текст/caption сообщения бота.
    """
    user_text = (message.text or "").strip()
    if not is_reply_to_bot_message(message):
        return None, user_text
    if message.quote is not None:
        return extract_quoted_text(message), user_text
    return _replied_bot_plain_text(message), user_text


def has_neurotext_message_input(message: Message) -> bool:
    """Есть текст, фото, документ или Reply на сообщение бота."""
    if (message.text or "").strip():
        return True
    if message.photo or message.document:
        return True
    return is_reply_to_bot_message(message)
