"""Музыкальная студия NeuroMule 🐎⚡️ на Suno AI v4.

Здесь собрано всё, что относится к новому music-флоу:

* Карточка «Музыкальная студия» с 3 кнопками режимов и жёстким гардом
  FREE / нехватки 15 💎.
* FSM-collector :class:`platforms.telegram_states.MusicFlow` для 3 режимов
  (ИИ-сценарист, свой текст, инструментал).
* Апсейл-клавиатура :func:`result_music_keyboard_pro` обрабатывается тут же:
  ``Продлить трек (+1 мин)`` запускает реальный повторный рендер через
  ``run_music_generation_turn`` с ``continue_clip_id``, остальные кнопки
  показывают честный «🚧 скоро» алерт и НЕ списывают 💎.

Соблюдаем правила ТЗ:

* FREE → ``callback.answer(show_alert=True)`` с :data:`msg.TXT_MUSIC_FREE_BLOCKED_ALERT`.
* Платный с < 15 💎 → HTML-карточка нехватки + ``crystals_shop_inline_card_keyboard``.
* Никакой энергии: ``run_music_generation_turn`` ходит через
  ``billing.spend_music`` → ``atomic_spend(crystals_only=True)``.
* Опция DUO (ULTRA 1 мес.): гард учитывает партнёра через ``resolve_duo_owner``,
  списание на owner-кошельке уже выполняет ``store._resolve_wallet_id``.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from content import messages as msg
from content.inline_keyboards import (
    music_studio_keyboard,
    result_music_keyboard_pro,
)
from platforms.telegram_states import MusicFlow, UserFlow
from platforms.tariffs_center import crystals_shop_inline_card_keyboard
from services import last_music_request
from services.billing import billing
from services.billing.store import load_user_billing
from services.family_sharing import resolve_duo_owner
from services.tariffs import TariffName, can_use_music, normalize_tariff
from services.use_cases.music_generation_turn import (
    MusicGenOutcome,
    run_music_generation_turn,
)

logger = logging.getLogger(__name__)

router = Router(name="music_studio")

MUSIC_COST = 15
MUSIC_LYRICS_MAX = 1500


async def _is_duo_partner(uid: int) -> bool:
    """True, если пользователь — привязанный member действующей ULTRA-семьи."""

    try:
        owner_id = await resolve_duo_owner(uid)
    except Exception:  # pragma: no cover - защита от падений DUO-модуля
        logger.warning("resolve_duo_owner failed for uid=%s", uid, exc_info=True)
        return False
    return owner_id != uid


async def _music_access_snapshot(uid: int) -> tuple[TariffName, int, bool]:
    """Эффективный (тариф, баланс, флаг ULTRA-семьи) для гарда Музстудии."""

    state = await load_user_billing(uid)
    tariff = normalize_tariff(state.current_tariff.value)
    is_duo_partner = await _is_duo_partner(uid)
    return tariff, int(state.crystals), is_duo_partner


def _music_blocked_for_free(tariff: TariffName, is_duo_partner: bool) -> bool:
    """FREE без ULTRA-семьи → доступ закрыт."""

    if is_duo_partner:
        return False
    return not can_use_music(tariff)


async def _answer_html(message: Message, text: str, **kwargs: Any) -> Message | None:
    """``message.answer`` с ParseMode HTML и аккуратным fallback на plain."""

    try:
        return await message.answer(text, parse_mode=ParseMode.HTML, **kwargs)
    except TelegramBadRequest:
        kwargs.pop("reply_markup", None)
        return await message.answer(text, **kwargs)


async def open_music_studio_screen(
    message: Message, uid: int, *, state: FSMContext | None = None
) -> None:
    """Главный экран Музстудии: гард + HTML-карточка + 3 кнопки режимов."""

    if state is not None:
        await state.clear()

    tariff, balance, is_duo_partner = await _music_access_snapshot(uid)

    if _music_blocked_for_free(tariff, is_duo_partner):
        await _answer_html(message, msg.TXT_MUSIC_FREE_BLOCKED_ALERT)
        return

    if balance < MUSIC_COST:
        await _answer_html(
            message,
            msg.TXT_MUSIC_INSUFFICIENT_CRYSTALS.format(balance=balance),
            reply_markup=crystals_shop_inline_card_keyboard(),
        )
        return

    await _answer_html(
        message,
        msg.TXT_MUSIC_STUDIO_INTRO,
        reply_markup=music_studio_keyboard(),
    )


async def _guard_callback(
    callback: CallbackQuery,
) -> tuple[TariffName, int, bool] | None:
    """Гард на любом музыкальном callback: FREE / нехватки 💎."""

    uid = callback.from_user.id
    tariff, balance, is_duo_partner = await _music_access_snapshot(uid)

    if _music_blocked_for_free(tariff, is_duo_partner):
        await callback.answer(msg.TXT_MUSIC_FREE_BLOCKED_ALERT, show_alert=True)
        return None

    if balance < MUSIC_COST:
        if callback.message is not None:
            await _answer_html(
                callback.message,
                msg.TXT_MUSIC_INSUFFICIENT_CRYSTALS.format(balance=balance),
                reply_markup=crystals_shop_inline_card_keyboard(),
            )
        await callback.answer()
        return None

    return tariff, balance, is_duo_partner


# ─── Entrypoints ────────────────────────────────────────────────────────────

@router.callback_query(F.data == msg.CB_CREATE_MUSIC)
async def cb_open_music_studio(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await open_music_studio_screen(callback.message, callback.from_user.id, state=state)
    await callback.answer()


@router.message(F.text == msg.BTN_REPLY_MUSIC)
async def reply_open_music_studio(message: Message, state: FSMContext) -> None:
    await open_music_studio_screen(message, message.from_user.id, state=state)


# ─── Mode pickers ───────────────────────────────────────────────────────────

@router.callback_query(F.data == msg.CB_MUSIC_MODE_AI)
async def cb_mode_ai(callback: CallbackQuery, state: FSMContext) -> None:
    if await _guard_callback(callback) is None:
        return
    await state.set_state(MusicFlow.waiting_for_style_prompt)
    if callback.message is not None:
        await _answer_html(callback.message, msg.TXT_MUSIC_ASK_STYLE_AI)
    await callback.answer()


@router.callback_query(F.data == msg.CB_MUSIC_MODE_CUSTOM)
async def cb_mode_custom(callback: CallbackQuery, state: FSMContext) -> None:
    if await _guard_callback(callback) is None:
        return
    await state.set_state(MusicFlow.waiting_for_custom_lyrics)
    await state.update_data(lyrics_text=None)
    if callback.message is not None:
        await _answer_html(callback.message, msg.TXT_MUSIC_ASK_LYRICS)
    await callback.answer()


@router.callback_query(F.data == msg.CB_MUSIC_MODE_INSTRUMENTAL)
async def cb_mode_instrumental(callback: CallbackQuery, state: FSMContext) -> None:
    if await _guard_callback(callback) is None:
        return
    await state.set_state(MusicFlow.waiting_for_instrumental_style)
    if callback.message is not None:
        await _answer_html(callback.message, msg.TXT_MUSIC_ASK_INSTRUMENTAL_STYLE)
    await callback.answer()


# ─── FSM collector ──────────────────────────────────────────────────────────

async def _launch_music_turn(
    message: Message,
    *,
    state: FSMContext,
    style_prompt: str,
    mode: str,
    lyrics: str | None,
) -> None:
    """Финальная стадия любого режима: списание 15 💎 + постановка в очередь."""

    uid = message.from_user.id
    await state.clear()

    result = await run_music_generation_turn(
        uid=uid,
        style_prompt=style_prompt,
        bot=message.bot,
        chat_id=message.chat.id,
        mode=mode,  # type: ignore[arg-type]
        lyrics=lyrics,
    )

    if result.outcome is MusicGenOutcome.SUCCESS:
        return
    if result.outcome is MusicGenOutcome.NEED_HINT:
        await _answer_html(message, msg.TXT_MUSIC_ASK_STYLE_AI)
        return
    if result.outcome is MusicGenOutcome.FREE_PREMIUM_BLOCKED:
        await _answer_html(message, msg.TXT_MUSIC_FREE_BLOCKED_ALERT)
        return
    if result.outcome is MusicGenOutcome.FORBIDDEN_BY_TARIFF:
        await _answer_html(message, msg.TXT_MUSIC_FREE_BLOCKED_ALERT)
        return
    if result.outcome is MusicGenOutcome.INSUFFICIENT_BALANCE:
        _, balance, _ = await _music_access_snapshot(uid)
        await _answer_html(
            message,
            msg.TXT_MUSIC_INSUFFICIENT_CRYSTALS.format(balance=balance),
            reply_markup=crystals_shop_inline_card_keyboard(),
        )


@router.message(MusicFlow.waiting_for_style_prompt, F.text)
async def fsm_collect_ai_style(message: Message, state: FSMContext) -> None:
    style = (message.text or "").strip()
    if not style:
        await _answer_html(message, msg.TXT_MUSIC_ASK_STYLE_AI)
        return
    await _launch_music_turn(
        message,
        state=state,
        style_prompt=style,
        mode="ai_lyrics",
        lyrics=None,
    )


@router.message(MusicFlow.waiting_for_custom_lyrics, F.text)
async def fsm_collect_custom_lyrics(message: Message, state: FSMContext) -> None:
    lyrics = (message.text or "").strip()
    if not lyrics:
        await _answer_html(message, msg.TXT_MUSIC_ASK_LYRICS)
        return
    if len(lyrics) > MUSIC_LYRICS_MAX:
        await _answer_html(message, msg.TXT_MUSIC_LYRICS_TOO_LONG)
        return
    await state.update_data(lyrics_text=lyrics)
    await state.set_state(MusicFlow.waiting_for_custom_style)
    await _answer_html(message, msg.TXT_MUSIC_ASK_STYLE_AFTER_LYRICS)


@router.message(MusicFlow.waiting_for_custom_style, F.text)
async def fsm_collect_custom_style(message: Message, state: FSMContext) -> None:
    style = (message.text or "").strip()
    if not style:
        await _answer_html(message, msg.TXT_MUSIC_ASK_STYLE_AFTER_LYRICS)
        return
    data = await state.get_data()
    lyrics = (data.get("lyrics_text") or "").strip() or None
    if not lyrics:
        await state.set_state(MusicFlow.waiting_for_custom_lyrics)
        await _answer_html(message, msg.TXT_MUSIC_ASK_LYRICS)
        return
    await _launch_music_turn(
        message,
        state=state,
        style_prompt=style,
        mode="custom_lyrics",
        lyrics=lyrics,
    )


@router.message(MusicFlow.waiting_for_instrumental_style, F.text)
async def fsm_collect_instrumental_style(message: Message, state: FSMContext) -> None:
    style = (message.text or "").strip()
    if not style:
        await _answer_html(message, msg.TXT_MUSIC_ASK_INSTRUMENTAL_STYLE)
        return
    await _launch_music_turn(
        message,
        state=state,
        style_prompt=style,
        mode="instrumental",
        lyrics=None,
    )


# Тихие fallback'и: пользователь прислал не-текст в нужном стейте.
@router.message(MusicFlow.waiting_for_style_prompt)
async def fsm_need_style(message: Message) -> None:
    await _answer_html(message, msg.TXT_MUSIC_ASK_STYLE_AI)


@router.message(MusicFlow.waiting_for_custom_lyrics)
async def fsm_need_lyrics(message: Message) -> None:
    await _answer_html(message, msg.TXT_MUSIC_ASK_LYRICS)


@router.message(MusicFlow.waiting_for_custom_style)
async def fsm_need_custom_style(message: Message) -> None:
    await _answer_html(message, msg.TXT_MUSIC_ASK_STYLE_AFTER_LYRICS)


@router.message(MusicFlow.waiting_for_instrumental_style)
async def fsm_need_instrumental_style(message: Message) -> None:
    await _answer_html(message, msg.TXT_MUSIC_ASK_INSTRUMENTAL_STYLE)


# ─── Upsell ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == msg.CB_MUSIC_EXTEND)
async def cb_music_extend(callback: CallbackQuery, state: FSMContext) -> None:
    """Продление трека (+1 мин): повторный Suno-рендер с ``continue_clip_id``."""

    if await _guard_callback(callback) is None:
        return

    last = last_music_request.get(callback.from_user.id)
    if last is None or callback.message is None:
        await callback.answer(msg.TXT_MUSIC_EXTEND_NO_HISTORY, show_alert=True)
        return

    await state.clear()
    await callback.answer(msg.TXT_MUSIC_EXTEND_QUEUED)

    await run_music_generation_turn(
        uid=callback.from_user.id,
        style_prompt=last.style,
        bot=callback.message.bot,
        chat_id=callback.message.chat.id,
        mode="instrumental" if last.make_instrumental else (
            "custom_lyrics" if last.lyrics else "ai_lyrics"
        ),
        lyrics=last.lyrics,
        continue_clip_id=last.clip_id,
    )


@router.callback_query(F.data == msg.CB_MUSIC_CLIP)
async def cb_music_clip(callback: CallbackQuery) -> None:
    """Видеоклип для Shorts (20 💎) — Coming Soon, без списания."""

    await callback.answer(msg.TXT_MUSIC_UPSELL_SOON, show_alert=True)


@router.callback_query(F.data == msg.CB_MUSIC_VOICE_CLONE)
async def cb_music_voice_clone(callback: CallbackQuery) -> None:
    """RVC клон голоса (10 💎) — Coming Soon, без списания."""

    await callback.answer(msg.TXT_MUSIC_UPSELL_SOON, show_alert=True)


@router.callback_query(F.data == msg.CB_MUSIC_PUBLISH)
async def cb_music_publish(callback: CallbackQuery) -> None:
    """Публикация на ИИ-Радио — Coming Soon (нет PUBLIC_CHANNEL_ID)."""

    await callback.answer(msg.TXT_MUSIC_PUBLISH_NO_CHANNEL, show_alert=True)


# Прокинуть символ result_music_keyboard_pro наружу для удобства тестов/импорта.
__all__ = (
    "router",
    "open_music_studio_screen",
    "result_music_keyboard_pro",
    "MUSIC_COST",
)
