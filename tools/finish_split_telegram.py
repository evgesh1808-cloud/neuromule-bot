"""Генерирует модули из telegram_bot.py и _extract_*.txt."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "platforms" / "telegram_bot.py"
lines = SRC.read_text(encoding="utf-8").splitlines()


def dedent_block(start: int, end: int) -> list[str]:
    chunk = lines[start - 1 : end]
    out: list[str] = []
    for line in chunk:
        out.append(line[4:] if line.startswith("    ") else line)
    return out


def write_module(path: Path, header: str, body_lines: list[str], *, router: bool = False) -> None:
    body = "\n".join(body_lines)
    if router:
        body = body.replace("@dp.", "@router.")
        body = body.replace("subscribed_cache.pop(uid, None)", "channel_sub().invalidate(uid)")
    footer = "\n\nrouter = Router()\n" if router else ""
    if router:
        # router declared before handlers — move to after imports in header
        header = header.replace(
            "from aiogram import Router\n",
            "",
        )
        header = "from aiogram import Router\n" + header
        content = header + "\n\nrouter = Router()\n\n" + body + "\n"
    else:
        content = header + "\n\n" + body + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print("wrote", path.relative_to(ROOT), len(body_lines), "lines")


HANDLER_HEADER = '''"""Telegram handlers (auto-split from telegram_bot)."""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, types
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
from platforms.handlers.deps import (
    bot,
    channel_sub,
    check_and_spend,
    is_subscribed,
    is_subscribed_cached,
    send_start_main_welcome,
)
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

def _is_admin(user_id: int) -> bool:
    return is_admin_user(user_id)
'''

KEYBOARDS_HEADER = '''"""Inline и Reply-клавиатуры Telegram."""
from __future__ import annotations

import html
import re

from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from content import messages as msg
from platforms.telegram_utils import _invite_switch_query, is_admin_user

_TICKET_USER_ID_RE = re.compile(r"ID:\\s*(?:<code>|`)(\\d+)(?:</code>|`)", re.IGNORECASE)
'''

UTILS_HEADER = '''"""Утилиты и фильтры Telegram-платформы."""
from __future__ import annotations

import html
import logging
import re

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from content import messages as msg
from services import payments_catalog as paycat
from services.use_cases.video_generation_turn import VideoGenOutcome, VideoGenResult

logger = logging.getLogger(__name__)

_TICKET_USER_ID_RE = re.compile(r"ID:\\s*(?:<code>|`)(\\d+)(?:</code>|`)", re.IGNORECASE)
'''

MIDDLEWARE_HEADER = '''"""Middleware Telegram-бота."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, types
from aiogram.types import TelegramObject

from config import settings
from content import messages as msg
from platforms.telegram_keyboards import channel_gate_markup, terms_accept_keyboard
from platforms.telegram_subscription import ChannelSubscription
from platforms.telegram_utils import is_admin_user
from services.repository import ensure_user, user_has_accepted_terms
'''


if __name__ == "__main__":
    # keyboards: 165-554 (1-based) but 165 is _invite - include from 165
    kb_body = dedent_block(165, 554)
    # move ticket helpers to utils only — strip from keyboards if duplicated
    write_module(ROOT / "platforms/telegram_keyboards.py", KEYBOARDS_HEADER, kb_body)

    utils_body = dedent_block(111, 163) + dedent_block(185, 218) + dedent_block(221, 224)
    utils_body += [
        "",
        "def _invite_switch_query() -> str:",
        '    q = msg.INVITE_SWITCH_QUERY_TEMPLATE.format(',
        '        bot_username=settings.telegram_bot_username.lstrip("@"),',
        "    )",
        "    return q[:256]",
        "",
    ]
    utils_body += dedent_block(342, 375)
    write_module(ROOT / "platforms/telegram_utils.py", UTILS_HEADER, utils_body)

    mw_body = dedent_block(152, 163)
    mw_body += [
        "",
        "def build_terms_gate_middleware() -> TermsGateMiddleware:",
        "    return TermsGateMiddleware()",
        "",
    ]
    # Terms + Channel from inside build_dispatcher - write manually in telegram_middleware.py
    print("Run manual middleware + handler conversion after this")
