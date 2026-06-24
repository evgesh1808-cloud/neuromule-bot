"""Уведомления Telegram для WB API worker."""

from __future__ import annotations

import logging
from typing import Protocol

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import settings
from platforms.table_mini_app_keyboard import build_table_mini_app_url
from services.wb_api.types import WbBatchDigest

logger = logging.getLogger(__name__)


class NotifierPort(Protocol):
    async def send_morning_analytics(
        self,
        user_id: int,
        *,
        digest: WbBatchDigest,
        report_id: int,
    ) -> None: ...


def format_morning_telegram_message(digest: WbBatchDigest) -> str:
    """Красивое утреннее сообщение для селлера."""
    profit = f"{digest.net_profit:,.0f}".replace(",", " ")
    lines = [
        "📊 <b>Ваша утренняя аналитика по API готова!</b>",
        f"🟢 За вчера чистая прибыль: <b>{profit} руб.</b>",
        f"📦 Лидер Группы А: <b>{digest.group_a_leader}</b>",
    ]
    if digest.oos_product and digest.oos_days is not None:
        fomo = f"{digest.fomo_rub:,.0f}".replace(",", " ")
        lines.append(
            f"🚨 Внимание: товар <b>{digest.oos_product}</b> закончится через "
            f"<b>{digest.oos_days}</b> дн.! Упущенная выгода: <b>{fomo} руб.</b>"
        )
    if digest.morning_insight:
        lines.extend(["", f"💡 <i>{digest.morning_insight}</i>"])
    lines.extend(
        [
            "",
            "Нажмите на кнопку ниже, чтобы открыть интерактивный дашборд Mini App "
            "с полным ABC-анализом и калькулятором гипотез!",
        ]
    )
    return "\n".join(lines)


class TelegramNotifierPort:
    """Отправка через aiogram Bot."""

    def __init__(self, bot: Bot | None = None) -> None:
        self._bot = bot

    def _bot_instance(self) -> Bot:
        if self._bot is not None:
            return self._bot
        if not settings.tg_token:
            raise RuntimeError("TG_TOKEN is not configured for WB notifier")
        return Bot(token=settings.tg_token)

    async def send_morning_analytics(
        self,
        user_id: int,
        *,
        digest: WbBatchDigest,
        report_id: int,
    ) -> None:
        bot = self._bot_instance()
        text = format_morning_telegram_message(digest)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📊 Открыть интерактивный дашборд",
                        web_app=WebAppInfo(url=build_table_mini_app_url(report_id)),
                    )
                ]
            ]
        )
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        await bot.session.close()
