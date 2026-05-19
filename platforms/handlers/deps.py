"""Runtime-зависимости Telegram-обработчиков (инициализируются в build_dispatcher)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message

from config import settings
from content import messages as msg
from platforms.telegram_keyboards import main_menu
from platforms.telegram_subscription import ChannelSubscription
from services import payments_catalog as paycat
from services.hd_logic import get_user
from services.repository import check_and_spend as spend_crystals
from services.use_cases.start_ui_turn import start_messages_link_preview_off

_bot: Bot | None = None
_channel_sub: ChannelSubscription | None = None
_is_subscribed: Callable[[int], Awaitable[bool]] | None = None
_is_subscribed_cached: Callable[[int], Awaitable[bool]] | None = None


def bind(bot: Bot, channel_sub: ChannelSubscription) -> None:
    global _bot, _channel_sub, _is_subscribed, _is_subscribed_cached
    _bot = bot
    _channel_sub = channel_sub
    _is_subscribed = channel_sub.as_is_subscribed()

    async def _cached(user_id: int) -> bool:
        return await channel_sub.is_subscribed_cached(user_id)

    _is_subscribed_cached = _cached


def bot() -> Bot:
    if _bot is None:
        raise RuntimeError("handlers.deps not initialized")
    return _bot


def channel_sub() -> ChannelSubscription:
    if _channel_sub is None:
        raise RuntimeError("handlers.deps not initialized")
    return _channel_sub()


async def is_subscribed(user_id: int) -> bool:
    if _is_subscribed is None:
        raise RuntimeError("handlers.deps not initialized")
    return await _is_subscribed(user_id)


async def is_subscribed_cached(user_id: int) -> bool:
    if _is_subscribed_cached is None:
        raise RuntimeError("handlers.deps not initialized")
    return await _is_subscribed_cached(user_id)


async def check_and_spend(target: Message, user_id: int, amount: int) -> bool:
    user = await get_user(user_id)
    balance = int(user["crystals"] or 0)
    if balance < amount:
        await target.answer(
            msg.TXT_NOT_ENOUGH_CRYSTALS.format(amount=amount, balance=balance),
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return False
    ok = await spend_crystals(user_id, amount)
    if not ok:
        user = await get_user(user_id)
        await target.answer(
            msg.TXT_NOT_ENOUGH_CRYSTALS.format(
                amount=amount,
                balance=int(user["crystals"] or 0),
            ),
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    return ok


async def send_start_main_welcome(target: Message, user_id: int) -> None:
    no_preview = start_messages_link_preview_off()
    await target.answer(
        msg.TXT_START_WELCOME,
        parse_mode=ParseMode.HTML,
        link_preview_options=no_preview,
    )
    await target.answer(
        msg.TXT_START_MAIN_MENU_PROMPT,
        reply_markup=main_menu(user_id),
    )
