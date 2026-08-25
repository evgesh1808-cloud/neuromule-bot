"""Публичные URL Mini App (Telegram WebApp / VK open_app)."""

from __future__ import annotations

import os
from urllib.parse import urlencode, urlparse

from config import settings


def is_valid_telegram_webapp_url(url: str | None) -> bool:
    """Telegram WebApp принимает только публичные ``https://`` URL."""
    text = (url or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _api_base_url() -> str:
    return (
        os.getenv("API_BASE_URL")
        or settings.api_base_url
        or settings.mini_app_api_base_url
        or ""
    ).strip().rstrip("/")


def resolve_super_app_url(*, append_api_base: bool = True) -> str | None:
    """
    URL главного хаба ``web/index.html`` (Super App).

    Приоритет: ``HD_WEBAPP_URL`` → ``API_BASE_URL``/``/web/``.
    """
    base = (os.getenv("HD_WEBAPP_URL") or settings.hd_webapp_url or "").strip()
    if not base:
        api_base = _api_base_url()
        if api_base:
            base = f"{api_base}/web/"
    if not base:
        return None
    normalized = base if "?" in base else base.rstrip("/") + "/"
    if not is_valid_telegram_webapp_url(normalized.split("?", 1)[0]):
        return None
    if not append_api_base:
        return normalized
    api_base = _api_base_url()
    if not api_base:
        return normalized
    sep = "&" if "?" in normalized else "?"
    return f"{normalized}{sep}{urlencode({'api_base': api_base})}"


def resolve_image_studio_webapp_url() -> str | None:
    """
    URL Super App / Studio для кнопок «🎨 Открыть Студию».

    Приоритет: ``WEBAPP_STUDIO_URL`` → ``WEBAPP_SHOP_URL`` → Super App hub.
    """
    for candidate in (settings.webapp_studio_url, settings.webapp_shop_url):
        url = (candidate or "").strip()
        if not url:
            continue
        normalized = url if "?" in url else url.rstrip("/") + "/"
        if is_valid_telegram_webapp_url(normalized.split("?", 1)[0]):
            return normalized

    hub = resolve_super_app_url(append_api_base=True)
    if hub:
        sep = "&" if "?" in hub else "?"
        return f"{hub}{sep}{urlencode({'tab': 'studio'})}"
    return None


def resolve_webapp_shop_url() -> str | None:
    """HTTPS URL Super App / магазина для ``WebAppInfo``."""
    url = (settings.webapp_shop_url or "").strip()
    if url:
        normalized = url if "?" in url else url.rstrip("/") + "/"
        if is_valid_telegram_webapp_url(normalized.split("?", 1)[0]):
            return normalized
    return resolve_super_app_url(append_api_base=True)
