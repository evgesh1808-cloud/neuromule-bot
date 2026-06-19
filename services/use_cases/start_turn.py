"""
Use-case: /start — регистрация пользователя, проверка подписки на канал (через колбэк), реферал из deep-link.

Платформенный слой передаёт ``is_subscribed`` (например, ``get_chat_member`` в Telegram).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from config import Settings
from services.repository import (
    ensure_user,
    get_user_row,
    try_set_referrer,
    user_has_accepted_terms,
)


class StartFlowOutcome(str, Enum):
    """Какой сценарий показать после ``/start``."""

    NEED_PAYWALL = "need_paywall"
    WELCOME_MAIN_MENU = "welcome_main_menu"
    # Обратная совместимость (старые тесты/импорты)
    NEED_TERMS = NEED_PAYWALL
    NEED_CHANNEL = NEED_PAYWALL


@dataclass(frozen=True)
class StartTurnResult:
    outcome: StartFlowOutcome
    template_kwargs: dict[str, object]


def parse_telegram_start_ref(start_command_text: str | None) -> int | None:
    """
    Извлекает inviter id из ``/start ref<id>`` или ``/start ref_<id>``.

    Возвращает ``None``, если аргумента нет или id не число.
    """
    if not start_command_text:
        return None
    parts = start_command_text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    payload = ""
    if arg.startswith("ref_"):
        payload = arg[4:]
    elif arg.startswith("ref"):
        payload = arg[3:]
    else:
        return None
    try:
        return int(payload)
    except ValueError:
        return None


async def run_start_turn(
    settings: Settings,
    user_id: int,
    username: str | None,
    start_command_text: str | None,
    *,
    is_subscribed: Callable[[int], Awaitable[bool]],
) -> StartTurnResult:
    """
    Обеспечивает строку в ``users``, при подписке — учёт реферера из ``/start``.

    Вход:
        settings — конфиг.
        user_id — Telegram id.
        username — ``from_user.username`` или ``None``.
        start_command_text — полный текст сообщения ``/start ...``.
        is_subscribed — асинхронная проверка подписки на канал: ``async (uid) -> bool`` (например ``get_chat_member``).

    Возвращает:
        ``StartTurnResult`` с исходом и ``template_kwargs`` для ``.format`` текстов приветствия.
    """
    await ensure_user(user_id, username)
    subscribed = await is_subscribed(user_id)
    accepted = await user_has_accepted_terms(user_id)
    if not (subscribed and accepted):
        return StartTurnResult(StartFlowOutcome.NEED_PAYWALL, {})

    row = await get_user_row(user_id)
    template_kwargs: dict[str, object] = dict(
        channel_url=settings.channel_url,
        text_daily_limit=settings.free_daily_chat_limit,
        photo_daily_limit=settings.free_daily_photo_limit,
        energy=row.energy,
        crystals=row.crystals,
    )

    inviter_id = parse_telegram_start_ref(start_command_text)
    if inviter_id is not None:
        await try_set_referrer(user_id, inviter_id)

    return StartTurnResult(StartFlowOutcome.WELCOME_MAIN_MENU, template_kwargs)
