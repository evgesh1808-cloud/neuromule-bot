"""Публичные URL Mini App «Студия» (Telegram WebApp / VK open_app)."""

from __future__ import annotations

from urllib.parse import urlparse

from config import settings


def is_valid_telegram_webapp_url(url: str | None) -> bool:
    """Telegram WebApp принимает только публичные ``https://`` URL."""
    text = (url or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme == "https" and bool(parsed.netloc)


def resolve_image_studio_webapp_url() -> str | None:
    """
    URL фронта ``webapp/`` для кнопок «🎨 Открыть Студию».

    Приоритет: ``WEBAPP_STUDIO_URL`` → ``WEBAPP_SHOP_URL`` → ``MINI_APP_API_BASE_URL/webapp/``.
    Невалидные ``http://`` / localhost не возвращаются — иначе Telegram отклоняет всю клавиатуру.
    """
    for candidate in (settings.webapp_studio_url, settings.webapp_shop_url):
        url = (candidate or "").strip()
        if not url:
            continue
        normalized = url if "?" in url else url.rstrip("/") + "/"
        if is_valid_telegram_webapp_url(normalized):
            return normalized

    api_base = (settings.mini_app_api_base_url or "").strip().rstrip("/")
    if api_base:
        candidate = f"{api_base}/webapp/"
        if is_valid_telegram_webapp_url(candidate):
            return candidate
    return None


def resolve_webapp_shop_url() -> str | None:
    """HTTPS URL магазина для ``WebAppInfo``; ``None`` если URL не подходит Telegram."""
    url = (settings.webapp_shop_url or "").strip()
    if not url:
        return None
    normalized = url if "?" in url else url.rstrip("/") + "/"
    if is_valid_telegram_webapp_url(normalized):
        return normalized
    return None
