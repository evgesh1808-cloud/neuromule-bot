"""Plain-text для VK: убираем Telegram HTML и Markdown-разметку."""

from __future__ import annotations

import re

from services.telegram_safe_text import sanitize_telegram_plain_text

__all__ = ("sanitize_telegram_plain_text", "vk_plain_text")

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_LINK_RE = re.compile(r"\[(.+?)\]\([^)]+\)")


def vk_plain_text(text: str, *, max_len: int = 3900) -> str:
    """
    Текст для ``messages.send`` без parse_mode.

    Снимает ``<b>``/``<i>`` (Telegram HTML) и типичный Markdown (**/_/[link](url)).
    """
    if not text:
        return ""
    plain = sanitize_telegram_plain_text(text, max_len=max_len + 256)
    plain = _MD_BOLD_RE.sub(r"\1", plain)
    plain = _MD_ITALIC_RE.sub(r"\1", plain)
    plain = _MD_LINK_RE.sub(r"\1", plain)
    plain = plain.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(plain) > max_len:
        plain = plain[: max_len - 1].rstrip() + "…"
    return plain
