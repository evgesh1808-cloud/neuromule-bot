"""FSM-состояния Telegram-бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class UserFlow(StatesGroup):
    waiting_for_text_prompt = State()
    waiting_for_photo = State()
    waiting_for_video = State()
    waiting_for_video_prank_photo = State()
    waiting_for_music = State()  # legacy: одношаговый ввод стиля
    waiting_for_animate = State()
    waiting_for_upscale_photo = State()
    waiting_hd_birth_data = State()
    waiting_advice_birth = State()
    WAITING_PARTNER_DATA = State()
    waiting_promo_code = State()
    waiting_for_memory = State()
    waiting_family_member_id = State()
    waiting_for_review = State()


class MusicFlow(StatesGroup):
    """3-ступенчатая Suno-студия NeuroMule 🐎⚡️.

    - ``waiting_for_style_prompt`` — режим «ИИ пишет текст + Стиль»: один шаг,
      пользователь даёт описание стиля, ИИ + Suno сочиняют и текст, и музыку.
    - ``waiting_for_custom_lyrics`` → ``waiting_for_custom_style`` — режим
      «Свой текст»: сперва ловим lyrics, потом стиль.
    - ``waiting_for_instrumental_style`` — режим «Только музыка»:
      ``make_instrumental=True``, lyrics не запрашиваются.
    """

    waiting_for_style_prompt = State()
    waiting_for_custom_lyrics = State()
    waiting_for_custom_style = State()
    waiting_for_instrumental_style = State()


class AdminStates(StatesGroup):
    waiting_for_crystals = State()
    waiting_for_broadcast = State()


class FeedbackStates(StatesGroup):
    waiting_support_message = State("wait_support_message")
    waiting_for_user_question = waiting_support_message  # legacy alias


class WBAuditingStates(StatesGroup):
    """FSM состояния для модуля ИИ-Аналитик (Excel) — Wildberries."""

    wait_for_tax = State()          # Шаг 1: выбор налоговой ставки (стандарт WB)
    wait_for_xlsx = State()         # Шаг 2: ожидание .xlsx / .csv отчёта


class OzonAuditingStates(StatesGroup):
    """Ожидание финансового отчёта Ozon."""

    wait_for_xlsx = State()


class YandexAuditingStates(StatesGroup):
    """Ожидание финансового отчёта Яндекс.Маркет."""

    wait_for_xlsx = State()


class OneCAuditingStates(StatesGroup):
    """Ожидание выгрузки 1С / МойСклад."""

    wait_for_xlsx = State()
