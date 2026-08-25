"""Нативная кнопка Telegram «📱 Studio» (MenuButtonWebApp)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import settings
from content import messages as msg
from platforms.webapp_urls import is_valid_telegram_webapp_url

logger = logging.getLogger(__name__)


def _normalize_webapp_url(url: str) -> str:
    text = url.strip()
    return text if "?" in text else text.rstrip("/") + "/"


def resolve_studio_webapp_url() -> str | None:
    """
    URL Mini App для ``set_chat_menu_button``.

    Приоритет: ``WEBAPP_STUDIO_URL`` → ``WEBAPP_SHOP_URL`` → база ``WEBAPP_TABLE_REPORTS_URL``.
    ``report_id`` в шаблоне отбрасывается — глобальная кнопка открывает хаб Studio;
    конкретный отчёт подставляется в query при доставке из бота.

    Только ``https://`` — иначе Telegram отклоняет MenuButtonWebApp и бот не стартует.
    """
    for candidate in (settings.webapp_studio_url, settings.webapp_shop_url):
        url = (candidate or "").strip()
        if not url:
            continue
        normalized = _normalize_webapp_url(url)
        if is_valid_telegram_webapp_url(normalized):
            return normalized
        logger.warning(
            "Studio webapp URL skipped (need https): %r",
            normalized,
        )

    template = (settings.webapp_table_reports_url or "").strip()
    if not template:
        from platforms.webapp_urls import resolve_super_app_url

        return resolve_super_app_url(append_api_base=True)

    if "{report_id}" in template:
        base = template.split("{report_id}", 1)[0]
    else:
        base = template
    base = base.split("?", 1)[0].rstrip("/?&=")
    if not base:
        return None
    normalized = f"{base}/"
    if is_valid_telegram_webapp_url(normalized):
        return normalized
    logger.warning(
        "Studio webapp URL skipped from WEBAPP_TABLE_REPORTS_URL (need https): %r",
        normalized,
    )
    return None


async def setup_studio_menu_button(bot: Bot) -> bool:
    """
    Регистрирует нативную кнопку «📱 Studio» слева внизу в Telegram.

    Вызывается из ``platforms.telegram_bot.run_telegram`` после проверки API.
    Ошибка Telegram (невалидный URL) **не** роняет polling — только WARNING.
    Возвращает ``True``, если кнопка успешно установлена.
    """
    url = resolve_studio_webapp_url()
    if not url:
        logger.warning(
            "Studio MenuButtonWebApp skipped: set WEBAPP_STUDIO_URL "
            "(or HTTPS WEBAPP_SHOP_URL / WEBAPP_TABLE_REPORTS_URL) in .env"
        )
        return False

    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text=msg.BTN_STUDIO_MENU,
                web_app=WebAppInfo(url=url),
            )
        )
    except TelegramBadRequest as exc:
        logger.warning(
            "Studio MenuButtonWebApp rejected by Telegram url=%s: %s",
            url,
            exc,
        )
        return False

    logger.info("Studio MenuButtonWebApp set: text=%r url=%s", msg.BTN_STUDIO_MENU, url)
    return True
