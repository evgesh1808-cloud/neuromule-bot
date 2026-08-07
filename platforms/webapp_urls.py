"""Публичные URL Mini App «Студия» (Telegram WebApp / VK open_app)."""

from __future__ import annotations

from config import settings


def resolve_image_studio_webapp_url() -> str | None:
    """
    URL фронта ``webapp/`` для кнопок «🎨 Открыть Студию».

    Приоритет: ``WEBAPP_STUDIO_URL`` → ``MINI_APP_API_BASE_URL/webapp/``.
    """
    for candidate in (settings.webapp_studio_url, settings.webapp_shop_url):
        url = (candidate or "").strip()
        if url:
            return url if "?" in url else url.rstrip("/") + "/"

    api_base = (settings.mini_app_api_base_url or "").strip().rstrip("/")
    if api_base:
        return f"{api_base}/webapp/"
    return None
