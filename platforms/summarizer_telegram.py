"""Telegram-роутер саммари (aiogram 3.x) — только режим «📄 Саммари» в ИИ-Ассистенте."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from platforms.summarizer_flow import REPLY_NAV_BUTTON_TEXTS, handle_summary_neurotext_message
from platforms.telegram_states import UserFlow

logger = logging.getLogger(__name__)

summarizer_router = Router(name="summarizer")


class SummaryRoleFilter(BaseFilter):
    """Активен только после кнопки «📄 Саммари» (``text_role=summary`` в FSM)."""

    async def __call__(self, _event: Message, state: FSMContext) -> bool:
        data = await state.get_data()
        return str(data.get("text_role") or "").strip().lower() == "summary"


class SummaryUserTextFilter(BaseFilter):
    """Не перехватывать Reply-кнопки меню («🎨 Создать», «🚀 Тарифы» и т.д.)."""

    async def __call__(self, message: Message) -> bool:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        return text not in REPLY_NAV_BUTTON_TEXTS


_summary_state = StateFilter(UserFlow.waiting_for_text_prompt)
_summary_role = SummaryRoleFilter()
_summary_text = SummaryUserTextFilter()


@summarizer_router.message(_summary_state, _summary_role, F.document)
@summarizer_router.message(_summary_state, _summary_role, F.photo)
async def summary_mode_input_media(message: Message, state: FSMContext) -> None:
    await handle_summary_neurotext_message(message, state)


@summarizer_router.message(_summary_state, _summary_role, _summary_text)
async def summary_mode_input_text(message: Message, state: FSMContext) -> None:
    await handle_summary_neurotext_message(message, state)
