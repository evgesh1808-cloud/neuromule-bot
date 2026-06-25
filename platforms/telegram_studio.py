"""Нативная кнопка Telegram «📱 Studio» (MenuButtonWebApp)."""

from __future__ import annotations

from config import settings


def resolve_studio_webapp_url() -> str | None:
    """
    URL Mini App для ``set_chat_menu_button``.

    Приоритет: ``WEBAPP_STUDIO_URL`` → ``WEBAPP_SHOP_URL`` → база ``WEBAPP_TABLE_REPORTS_URL``.
    """
    for candidate in (settings.webapp_studio_url, settings.webapp_shop_url):
        url = (candidate or "").strip()
        if url:
            return url

    template = (settings.webapp_table_reports_url or "").strip()
    if not template:
        return None

    if "{report_id}" in template:
        base = template.split("{report_id}", 1)[0]
    else:
        base = template
    base = base.split("?", 1)[0].rstrip("/?&=")
    return f"{base}/" if base else None
