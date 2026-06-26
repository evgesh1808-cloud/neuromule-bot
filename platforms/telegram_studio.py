"""Нативная кнопка Telegram «📱 Studio» (MenuButtonWebApp)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import settings
from content import messages as msg

logger = logging.getLogger(__name__)


def resolve_studio_webapp_url() -> str | None:
    """
    URL Mini App для ``set_chat_menu_button``.

    Приоритет: ``WEBAPP_STUDIO_URL`` → ``WEBAPP_SHOP_URL`` → база ``WEBAPP_TABLE_REPORTS_URL``.
  ``report_id`` в шаблоне отбрасывается — глобальная кнопка открывает хаб Studio;
  конкретный отчёт подставляется в query при доставке из бота.
    """
    for candidate in (settings.webapp_studio_url, settings.webapp_shop_url):
        url = (candidate or "").strip()
        if url:
            return url.rstrip("/") + "/" if "?" not in url else url

    template = (settings.webapp_table_reports_url or "").strip()
    if not template:
        return None

    if "{report_id}" in template:
        base = template.split("{report_id}", 1)[0]
    else:
        base = template
    base = base.split("?", 1)[0].rstrip("/?&=")
    return f"{base}/" if base else None


async def setup_studio_menu_button(bot: Bot) -> bool:
    """
    Регистрирует нативную кнопку «📱 Studio» слева внизу в Telegram.

    Вызывается из ``platforms.telegram_bot.run_telegram`` после проверки API.
    Возвращает ``True``, если кнопка успешно установлена.
    """
    url = resolve_studio_webapp_url()
    if not url:
        logger.warning(
            "Studio MenuButtonWebApp skipped: set WEBAPP_STUDIO_URL "
            "(or WEBAPP_SHOP_URL / WEBAPP_TABLE_REPORTS_URL) in .env"
        )
        return False

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text=msg.BTN_STUDIO_MENU,
            web_app=WebAppInfo(url=url),
        )
    )
    logger.info("Studio MenuButtonWebApp set: text=%r url=%s", msg.BTN_STUDIO_MENU, url)
    return True
