"""FSM-состояния Telegram-бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class UserFlow(StatesGroup):
    waiting_for_text_prompt = State()
    waiting_for_photo = State()
    waiting_for_video = State()
    waiting_for_video_prank_photo = State()
    waiting_for_music = State()
    waiting_for_animate = State()
    waiting_for_upscale_photo = State()
    waiting_hd_birth_data = State()
    waiting_advice_birth = State()
    WAITING_PARTNER_DATA = State()
    waiting_promo_code = State()


class AdminStates(StatesGroup):
    waiting_for_crystals = State()
    waiting_for_broadcast = State()


class FeedbackStates(StatesGroup):
    waiting_for_user_question = State()
