"""Безопасный plain-text для Telegram (без parse_mode)."""

from __future__ import annotations

import re

_TAG_RE = re.compile(r"<[^>]+>")
_BROKEN_ENTITY_RE = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")


def sanitize_telegram_plain_text(text: str, *, max_len: int = 4090) -> str:
    """
    Убирает HTML-теги и обрезает длину для ``edit_message_text`` / ``answer`` без parse_mode.
    Markdown-символы оставляем как литералы (режим по умолчанию).
    """
    if not text:
        return ""
    cleaned = _TAG_RE.sub("", text)
    cleaned = _BROKEN_ENTITY_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned
