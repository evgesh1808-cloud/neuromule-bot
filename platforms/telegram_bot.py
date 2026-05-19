"""Telegram-интерфейс (aiogram): сборка Dispatcher и точка входа polling."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher

from config import settings
from platforms.handlers import deps, register_all
from platforms.telegram_middleware import (
    ChannelGateMiddleware,
    DailyResetMiddleware,
    TermsGateMiddleware,
)
from platforms.telegram_subscription import ChannelSubscription
from platforms.telegram_states import AdminStates, FeedbackStates, UserFlow
from platforms.telegram_utils import HelpInstructionWordFilter, is_admin_user
from services.app_logging import setup_logging
from services.dialog_write_worker import start_dialog_write_worker
from services.repository import init_db

# Обратная совместимость для импортов из старого монолита
from platforms.telegram_keyboards import (  # noqa: F401
    cabinet_keyboard,
    channel_gate_markup,
    create_menu,
    main_menu,
    support_faq_keyboard,
    terms_accept_keyboard,
)
from platforms.telegram_utils import (  # noqa: F401
    notify_admins_about_payment,
    send_same_as_instruction_button,
)

logger = logging.getLogger(__name__)


def build_dispatcher() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.tg_token)
    dp = Dispatcher()
    channel_sub = ChannelSubscription(bot)
    deps.bind(bot, channel_sub)

    daily_reset = DailyResetMiddleware()
    dp.message.outer_middleware(daily_reset)
    dp.callback_query.outer_middleware(daily_reset)
    dp.pre_checkout_query.outer_middleware(daily_reset)

    terms_gate = TermsGateMiddleware()
    dp.message.outer_middleware(terms_gate)
    dp.callback_query.outer_middleware(terms_gate)
    dp.pre_checkout_query.outer_middleware(terms_gate)

    channel_gate = ChannelGateMiddleware(channel_sub)
    dp.message.outer_middleware(channel_gate)
    dp.callback_query.outer_middleware(channel_gate)

    register_all(dp)
    return bot, dp


async def run_telegram() -> None:
    setup_logging(settings)
    if not settings.tg_token:
        raise RuntimeError("Задайте TG_TOKEN в .env")
    if not settings.openrouter_key:
        raise RuntimeError("Задайте OPENROUTER_API_KEY в .env")
    await init_db(settings.promo_seeds)
    await start_dialog_write_worker()
    bot, dp = build_dispatcher()
    print(f"{settings.bot_name} telegram: polling started.")
    await dp.start_polling(bot)
