"""Собирает telegram_keyboards, telegram_utils, handler-модули из telegram_bot.py."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "platforms" / "telegram_bot.py"
lines = SRC.read_text(encoding="utf-8").splitlines()


def raw(start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end]) + "\n"


def dedent(start: int, end: int) -> str:
    out: list[str] = []
    for line in lines[start - 1 : end]:
        out.append(line[4:] if line.startswith("    ") else line)
    return "\n".join(out) + "\n"


UTILS = '''"""Утилиты и фильтры Telegram-платформы."""
from __future__ import annotations

import html
import logging
import re

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from content import messages as msg
from services import payments_catalog as paycat
from services.use_cases.video_generation_turn import VideoGenOutcome, VideoGenResult

logger = logging.getLogger(__name__)

_TICKET_USER_ID_RE = re.compile(r"ID:\\s*(?:<code>|`)(\\d+)(?:</code>|`)", re.IGNORECASE)

''' + raw(111, 121).replace("_HelpInstructionWordFilter", "HelpInstructionWordFilter") + raw(
    165, 224
) + raw(342, 375) + raw(444, 461)

KEYBOARDS = '''"""Inline и Reply-клавиатуры Telegram."""
from __future__ import annotations

import html
import re

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from content import messages as msg
from platforms.telegram_utils import _invite_switch_query, is_admin_user

_TICKET_USER_ID_RE = re.compile(r"ID:\\s*(?:<code>|`)(\\d+)(?:</code>|`)", re.IGNORECASE)

''' + raw(226, 340) + raw(377, 441) + raw(464, 553)

MIDDLEWARE = '''"""Middleware Telegram-бота."""
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

''' + raw(152, 162) + '''

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
'''

HANDLER_HEADER = '''"""Telegram handlers."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from io import BytesIO
from pathlib import Path

from aiogram import F, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from config import settings
from content import messages as msg
from content.video_menu import (
    CB_VIDEO_CAT_PREFIX,
    CB_VIDEO_EXTEND,
    CB_VIDEO_LONG,
    CB_VIDEO_PREFIX,
    video_category_menu,
    video_root_menu,
)
from platforms.handlers import deps
from platforms.telegram_keyboards import (
    cabinet_keyboard,
    channel_gate_markup,
    create_menu,
    get_admin_inline_keyboard,
    hd_menu,
    hd_pro_unlocked_keyboard,
    hd_report_sections_markup,
    image_model_menu,
    invite_limit_keyboard,
    main_menu,
    photo_tools_menu,
    service_rules_menu,
    support_faq_keyboard,
    terms_accept_keyboard,
    text_role_menu,
)
from platforms.telegram_states import AdminStates, FeedbackStates, UserFlow
from platforms.telegram_utils import (
    HelpInstructionWordFilter,
    _extract_ticket_user_id,
    _feedback_ticket_header,
    _reply_menu_button_texts,
    _reply_video_gen_result,
    is_admin_user,
    notify_admins_about_payment,
    send_same_as_instruction_button,
)
from services import hd_service
from services import payments_catalog as paycat
from services.billing import billing
from services.billing.store import refund_charge
from services.hd_logic import (
    birth_data_minimum_for_advice,
    change_user_crystals,
    create_pdf,
    daily_advice_user_profile_from_repo_user,
    format_premium_report,
    generate_daily_forecast,
    generate_premium_report,
    get_calculated_gates,
    get_dynamic_cta_for_today,
    get_user,
    parse_birth_for_daily_advice,
    parse_hd_request,
    parse_match_request,
    premium_report_from_json,
    premium_report_to_json,
    today_iso,
    try_consume_crystals,
    update_user,
)
from services.repository import (
    add_promo_code,
    clear_user_dialog_and_memory,
    commit_daily_advice,
    ensure_user,
    get_sales_stats,
    get_user_row,
    list_all_user_ids,
    rollback_daily_advice,
    sales_stats_as_dict,
    set_user_accepted_terms,
    try_begin_daily_advice,
    update_balance,
)
from services.telegram_safe_text import sanitize_telegram_plain_text
from services.use_cases.animate_generation_turn import AnimateGenOutcome, run_animate_generation_turn
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.telegram_chunks import answer_chat_text
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn
from services.use_cases.music_generation_turn import MusicGenOutcome, run_music_generation_turn
from services.use_cases.cabinet_turn import build_cabinet_view
from services.use_cases.payment_invoice_turn import InvoiceBuildOutcome, build_payment_invoice_draft
from services.use_cases.payment_shop_turn import build_tariffs_entry_text
from services.use_cases.payment_turn import PaymentApplyOutcome, run_successful_payment_apply
from services.use_cases.photo_generation_turn import PhotoGenOutcome, run_photo_generation_turn
from services.use_cases.promo_turn import PromoOutcome, run_promo_redeem
from services.use_cases.start_turn import StartFlowOutcome, run_start_turn
from services.use_cases.tariff_shop_nav_turn import TariffShopNavOutcome, resolve_tariff_shop_callback
from services.use_cases.video_generation_turn import (
    VideoGenOutcome,
    VideoGenResult,
    classify_scenario_pick,
    run_video_scenario_turn,
)

logger = logging.getLogger(__name__)

router = Router()

is_subscribed = deps.is_subscribed
is_subscribed_cached = deps.is_subscribed_cached
check_and_spend = deps.check_and_spend
send_start_main_welcome = deps.send_start_main_welcome
channel_sub = deps.channel_sub


def _is_admin(user_id: int) -> bool:
    return is_admin_user(user_id)

'''

EXTRACT_MAP = [
    ("start_admin.py", "_extract_start_admin.txt"),
    ("menu_support.py", "_extract_menu_support.txt"),
    ("generation_cb.py", "_extract_generation_cb.txt"),
    ("hd.py", "_extract_hd.txt"),
    ("generation_fsm.py", "_extract_generation_fsm.txt"),
    ("payment_misc.py", "_extract_payment_misc.txt"),
]


def convert_extract(name: str, extract_name: str) -> None:
    path = ROOT / "platforms" / "handlers" / extract_name
    body = path.read_text(encoding="utf-8")
    body = body.replace("@dp.", "@router.")
    body = body.replace("subscribed_cache.pop(uid, None)", "channel_sub().invalidate(uid)")
    body = body.replace("_HelpInstructionWordFilter()", "HelpInstructionWordFilter()")
    body = body.replace("await bot.", "await deps.bot().")
    body = body.replace("create_throttled_stream_reply(message, bot, settings)", "create_throttled_stream_reply(message, deps.bot(), settings)")
    body = body.replace("chat_action_loop(bot, message.chat.id", "chat_action_loop(deps.bot(), message.chat.id")
    body = body.replace("notify_admins_about_payment(bot,", "notify_admins_about_payment(deps.bot(),")
    if name == "generation_cb.py":
        body = "from platforms.handlers.start_admin import start_match_flow\n\n" + body
    out = ROOT / "platforms" / "handlers" / name
    out.write_text(HANDLER_HEADER + "\n" + body, encoding="utf-8")
    print("handler", name, len(body.splitlines()))


if __name__ == "__main__":
    (ROOT / "platforms" / "telegram_utils.py").write_text(UTILS, encoding="utf-8")
    (ROOT / "platforms" / "telegram_keyboards.py").write_text(KEYBOARDS, encoding="utf-8")
    (ROOT / "platforms" / "telegram_middleware.py").write_text(MIDDLEWARE, encoding="utf-8")
    print("wrote utils, keyboards, middleware")
    for name, extract in EXTRACT_MAP:
        convert_extract(name, extract)
