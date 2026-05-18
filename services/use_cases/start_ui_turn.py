"""
UI-настройки для ответов ``/start`` без привязки к конкретному хендлеру.

Отдельный модуль, чтобы в ``telegram_bot`` не плодить ``LinkPreviewOptions(is_disabled=True)``.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape

from aiogram.types import LinkPreviewOptions


def start_messages_link_preview_off() -> LinkPreviewOptions:
    """Отключает превью ссылок в стартовых сообщениях (канал, приветствие)."""
    return LinkPreviewOptions(is_disabled=True)


def format_start_message_html(template: str, template_kwargs: Mapping[str, object]) -> str:
    """
    Подставляет kwargs в HTML-шаблон приветствия (ParseMode.HTML).

    Экранирует ``channel_url`` для вставки в ``href`` и текста: символы ``&``, ``<``, ``>``
    не должны ломать разметку и парсер Telegram.
    """
    data = dict(template_kwargs)
    data.setdefault("energy", 30)
    data.setdefault("crystals", 0)
    url = data.get("channel_url")
    if url is not None:
        data["channel_url"] = escape(str(url).strip(), quote=False)
    return template.format(**data)
