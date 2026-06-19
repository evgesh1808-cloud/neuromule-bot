"""Middleware Telegram-бота."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from config import settings
from content import messages as msg
from platforms.telegram_subscription import ChannelSubscription
from platforms.telegram_utils import send_start_paywall_screen, send_terms_required_reminder
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
        if isinstance(event, types.Message):
            text = (event.text or "").strip()
            if text.startswith("/start"):
                return await handler(event, data)
        elif isinstance(event, types.CallbackQuery):
            # CB_ACCEPT_LEGAL_TOS (новый TOS-gate, PR-G) ОБЯЗАТЕЛЬНО в
            # whitelist'е — иначе юзер не сможет принять условия, ведь
            # на момент клика ``accepted_terms`` ещё False. Без этого
            # callback режется middleware'ом → handler не выполняется →
            # юзер видит paywall reminder вместо подтверждения.
            if (event.data or "") in (
                msg.CB_ACCEPT_RULES,
                msg.CB_CHECK_SUBSCRIPTION,
                msg.CB_RECHECK_SUBSCRIPTION,
                msg.CB_ACCEPT_LEGAL_TOS,
            ):
                return await handler(event, data)
        else:
            return await handler(event, data)

        if await user_has_accepted_terms(user.id):
            return await handler(event, data)

        if isinstance(event, types.Message):
            await send_terms_required_reminder(event)
        elif isinstance(event, types.CallbackQuery) and event.message is not None:
            await send_terms_required_reminder(event.message)
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
            # CB_ACCEPT_LEGAL_TOS (новый TOS-gate, PR-G) ОБЯЗАТЕЛЬНО в
            # whitelist'е — иначе юзер не сможет принять условия и
            # навсегда застрянет на TOS-карточке (см. TermsGate выше).
            if (event.data or "") in (
                msg.CB_CHECK_SUBSCRIPTION,
                msg.CB_RECHECK_SUBSCRIPTION,
                msg.CB_ACCEPT_RULES,
                msg.CB_ACCEPT_LEGAL_TOS,
            ):
                return await handler(event, data)
        else:
            return await handler(event, data)

        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if await user_has_accepted_terms(user.id) and await self._channel_sub.is_subscribed_cached(
            user.id
        ):
            return await handler(event, data)

        if isinstance(event, types.Message):
            await send_start_paywall_screen(event)
        elif isinstance(event, types.CallbackQuery) and event.message is not None:
            await send_start_paywall_screen(event.message)
            await event.answer()
        return None
