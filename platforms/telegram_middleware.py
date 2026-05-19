"""Middleware Telegram-бота."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from config import settings
from content import messages as msg
from platforms.telegram_keyboards import channel_gate_markup, terms_accept_keyboard
from platforms.telegram_subscription import ChannelSubscription
from platforms.telegram_utils import is_admin_user
from services.repository import ensure_user, user_has_accepted_terms

class DailyResetMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            await ensure_user(user.id, getattr(user, "username", None))
        return await handler(event, data)


class TermsGateMiddleware(BaseMiddleware):
    """Блокировка функций бота до принятия оферты (``accepted_terms``)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if is_admin_user(user.id):
            return await handler(event, data)
        if isinstance(event, types.Message):
            text = (event.text or "").strip()
            if text.startswith("/start"):
                return await handler(event, data)
        elif isinstance(event, types.CallbackQuery):
            if (event.data or "") == msg.CB_ACCEPT_RULES:
                return await handler(event, data)
        else:
            return await handler(event, data)

        if await user_has_accepted_terms(user.id):
            return await handler(event, data)

        markup = terms_accept_keyboard()
        if isinstance(event, types.Message):
            await event.answer(msg.TXT_TERMS_REQUIRED, reply_markup=markup)
        elif isinstance(event, types.CallbackQuery):
            await event.message.answer(msg.TXT_TERMS_REQUIRED, reply_markup=markup)
            await event.answer()
        return None


class ChannelGateMiddleware(BaseMiddleware):
    """Мягкая проверка подписки на канал."""

    def __init__(self, channel_sub: ChannelSubscription) -> None:
        self._channel_sub = channel_sub

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, types.Message):
            text = (event.text or "").strip()
            if text.startswith("/start"):
                return await handler(event, data)
        elif isinstance(event, types.CallbackQuery):
            if (event.data or "") in (msg.CB_CHECK_SUBSCRIPTION, msg.CB_ACCEPT_RULES):
                return await handler(event, data)
        else:
            return await handler(event, data)

        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if await self._channel_sub.is_subscribed_cached(user.id):
            return await handler(event, data)

        markup = channel_gate_markup()
        if isinstance(event, types.Message):
            await event.answer(msg.TXT_CHANNEL_GATE, reply_markup=markup)
        elif isinstance(event, types.CallbackQuery):
            await event.message.answer(msg.TXT_CHANNEL_GATE, reply_markup=markup)
            await event.answer()
        return None
