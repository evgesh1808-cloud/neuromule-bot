"""Runtime-зависимости Telegram-обработчиков (инициализируются в build_dispatcher)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message

from config import settings
from content import messages as msg
from platforms.telegram_subscription import ChannelSubscription
from services import payments_catalog as paycat
from services.god_mode import billing_bypass
from services.hd_logic import get_user
from services.repository import check_and_spend as spend_crystals

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
    if billing_bypass(user_id):
        return True
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


async def send_start_main_welcome(
    target: Message,
    user_id: int,
    *,
    state: object | None = None,
) -> None:
    from platforms.telegram_utils import send_activation_success

    await send_activation_success(target, user_id, state=state)
