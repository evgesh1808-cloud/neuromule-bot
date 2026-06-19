"""TOS-gate middleware: блокирует ВСЕ апдейты до явного принятия оферты.

Паттерн железобетонной защиты:

* Если у пользователя ``is_tos_accepted == False``, ни одно сообщение, ни
  одна inline-кнопка, ни инлайн-запрос НЕ должны попадать на бизнес-логику.
  Иначе можно случайно начислить реферальные кристаллы / списать ресурсы /
  отправить сгенерированный контент юзеру, который формально не принял ОПФ.
* Исключения (whitelist), которые проходят без проверки:
    – ``/start`` (нужно, чтобы показать сам TOS-gate карточку);
    – callback ``accept_legal_tos`` (старая TOS-карточка, нужно чтобы юзер
      мог принять условия);
    – callback ``check_subscription`` (новая TOS-заслонка из
      ``start_onboarding``: одна кнопка одновременно принимает оферту
      и проверяет подписку на канал; внутри handler'а вызывается
      ``set_user_accepted_terms`` — флаг ставится атомарно с показом
      главного меню);
    – ``PreCheckoutQuery`` / ``SuccessfulPayment`` (Telegram сам гасит,
      их безопасно пропустить — атомарность платежа отдельно валидируется
      в `successful_payment` handler'е).
* Любой другой апдейт от непринявшего юзера → middleware показывает
  HTML-карточку TOS-gate и обрывает цепочку handler'ов.

Это middleware-уровень страховки. Аналогичная проверка есть в /start, но
если кто-то соскочит на FSM или callback в обход /start (например, по
deep-link), middleware всё равно его остановит.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    Message,
    PreCheckoutQuery,
    TelegramObject,
)

from config import URL_PRIVACY_POLICY, URL_PUBLIC_OFFER, URL_SUBSCRIPTION_TERMS
from content import messages as msg
from services.tos import is_tos_accepted

logger = logging.getLogger(__name__)


def _tos_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=msg.TXT_TOS_ACCEPT_BTN,
                    callback_data=msg.CB_ACCEPT_LEGAL_TOS,
                )
            ]
        ]
    )


def _tos_gate_text() -> str:
    return msg.TXT_TOS_WELCOME_GATE.format(
        offer_url=URL_PUBLIC_OFFER,
        privacy_url=URL_PRIVACY_POLICY,
        subscription_url=URL_SUBSCRIPTION_TERMS,
    )


def _is_start_command(message: Message) -> bool:
    text = (message.text or "").strip()
    return text.startswith("/start")


# Все callback_data, через которые юзер ПРИНИМАЕТ оферту. Любой из них
# должен пропускаться TOS-gate, иначе юзер физически не сможет
# проставить флаг и навсегда застрянет на gate-карточке.
_ACCEPT_TOS_CALLBACKS: frozenset[str] = frozenset(
    {
        msg.CB_ACCEPT_LEGAL_TOS,
        msg.CB_CHECK_SUBSCRIPTION,
        # «✅ Я подписался(ась)» — UX-кнопка retry-проверки подписки в
        # start_onboarding. Это эквивалент CB_CHECK_SUBSCRIPTION, но с
        # другой клавиатурой (юзер только что вернулся из канала); тоже
        # принимает оферту в случае успеха.
        msg.CB_RECHECK_SUBSCRIPTION,
    }
)


def _is_accept_tos_callback(callback: CallbackQuery) -> bool:
    return (callback.data or "").strip() in _ACCEPT_TOS_CALLBACKS


class TosGateMiddleware(BaseMiddleware):
    """Жёсткий шлагбаум для всех апдейтов от непринявших TOS."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # PreCheckoutQuery от Telegram пропускаем — это валидация платежа,
        # завязка на TOS там не нужна (всё равно начисление идёт через
        # successful_payment, который попадает уже в Message выше).
        if isinstance(event, PreCheckoutQuery):
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        user_id = int(getattr(user, "id", 0) or 0)
        if user_id == 0:
            return await handler(event, data)

        # Быстрый whitelist: /start всегда проходит; «принимающие» callback'и
        # (``accept_legal_tos`` старого flow + ``check_subscription`` нового
        # start_onboarding) — тоже, иначе юзер не сможет принять условия.
        if isinstance(event, Message) and _is_start_command(event):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and _is_accept_tos_callback(event):
            return await handler(event, data)

        accepted = await is_tos_accepted(user_id)
        if accepted:
            return await handler(event, data)

        # ── НЕ принял TOS: блокируем и показываем gate-карточку ──────────
        logger.info("tos_gate: blocked event=%s user_id=%s", type(event).__name__, user_id)

        if isinstance(event, CallbackQuery):
            try:
                await event.answer(
                    "🐎 Сначала прими условия NeuroMule в /start.",
                    show_alert=True,
                )
            except Exception:
                logger.debug("tos_gate: callback.answer failed", exc_info=True)
            if event.message is not None:
                try:
                    await event.message.answer(
                        _tos_gate_text(),
                        parse_mode=ParseMode.HTML,
                        reply_markup=_tos_gate_keyboard(),
                        disable_web_page_preview=True,
                    )
                except Exception:
                    logger.debug("tos_gate: gate render failed", exc_info=True)
            return None

        if isinstance(event, Message):
            try:
                await event.answer(
                    _tos_gate_text(),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_tos_gate_keyboard(),
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.debug("tos_gate: gate render failed", exc_info=True)
            return None

        if isinstance(event, InlineQuery):
            # Инлайн-режим без принятия TOS — отдаём пустой результат.
            try:
                await event.answer(results=[], cache_time=1, is_personal=True)
            except Exception:
                logger.debug("tos_gate: empty inline answer failed", exc_info=True)
            return None

        # Любой иной тип апдейта — глушим без ответа.
        return None


__all__ = ("TosGateMiddleware",)
