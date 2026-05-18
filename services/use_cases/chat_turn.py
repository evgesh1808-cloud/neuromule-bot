"""
Use-case: один полный цикл «пользователь написал в свободный чат → ответ нейросети».

Сюда вынесены правила списания энергии, rate limit (БД / Redis), запись истории, вызов OpenRouter
и откаты при ошибке. Слой platforms (Telegram) только показывает результат пользователю.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from config import Settings
from services import conversation as conv
from services.ai_text import StreamCallback, ask_ai_messages, estimate_messages_prompt_tokens
from services.dialog_write_worker import commit_assistant_turn_queued
from services.rate_limit_service import allow_request, rollback_last
from services.repository import (
    dialog_append,
    dialog_pop_last_for_user,
    get_user_row,
    try_consume_chat_credit,
    update_balance,
)
from services.tariffs import normalize_tariff, text_models_for_tariff

logger = logging.getLogger(__name__)


class ChatTurnOutcome(str, Enum):
    """Результат попытки обработать одно пользовательское сообщение в чате."""

    SUCCESS = "success"
    EMPTY_INPUT = "empty_input"
    RATE_LIMITED = "rate_limited"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    CONTEXT_TOO_LARGE = "context_too_large"
    AI_FAILED = "ai_failed"


@dataclass(frozen=True)
class ChatTurnResult:
    """
    Результат use-case ``run_chat_turn``.

    Поля:
        outcome — итоговый статус (см. ``ChatTurnOutcome``).
        assistant_message — текст ответа модели; заполнено только при ``SUCCESS``.
    """

    outcome: ChatTurnOutcome
    assistant_message: str | None = None


async def run_chat_turn(
    settings: Settings,
    user_id: int,
    raw_user_text: str,
    *,
    send_typing: Callable[[], Awaitable[None]] | None = None,
    http_client: object | None = None,
    stream_callback: StreamCallback | None = None,
    text_role: str = "standard",
) -> ChatTurnResult:
    """
    Выполняет один «ход» чата с нейросетью (без отправки сообщений в Telegram).

    Вход:
        settings — конфиг приложения.
        user_id — Telegram user id.
        raw_user_text — текст пользователя (обрезка по символам — на границе Telegram).
        send_typing — необязательный колбэк «показать typing» перед долгим запросом к API.
        http_client — опциональный ``httpx.AsyncClient`` (тесты).
        stream_callback — если задан, запрос к модели идёт в SSE-режиме (для live-редактирования в Telegram).

    Возвращает:
        ``ChatTurnResult`` с ``outcome`` и при успехе — ``assistant_message``.

    Побочные эффекты:
        при SUCCESS — в БД добавлены user+assistant, обрезана история, запланировано обновление памяти;
        при INSUFFICIENT / RATE_LIMITED / AI_FAILED — энергия и история согласованы (откаты где нужно).
    """
    if not (raw_user_text or "").strip():
        return ChatTurnResult(outcome=ChatTurnOutcome.EMPTY_INPUT)

    if not await allow_request(settings, user_id, settings.chat_rate_limit_per_minute):
        return ChatTurnResult(outcome=ChatTurnOutcome.RATE_LIMITED)

    row = await get_user_row(user_id)
    tariff = normalize_tariff(row.tariff)
    spent_field = await try_consume_chat_credit(user_id)
    if spent_field is None:
        await rollback_last(settings, user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.INSUFFICIENT_BALANCE)

    await dialog_append(user_id, "user", raw_user_text)
    payload = await conv.build_openrouter_messages(settings, user_id, text_role)

    est_tokens = estimate_messages_prompt_tokens(payload, settings=settings)
    if est_tokens > settings.chat_max_context_tokens_est:
        await dialog_pop_last_for_user(user_id)
        await update_balance(user_id, spent_field, 1)
        await rollback_last(settings, user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.CONTEXT_TOO_LARGE)

    if send_typing is not None:
        try:
            await send_typing()
        except Exception:
            logger.debug("send_typing failed", exc_info=True)

    try:
        ans = await ask_ai_messages(
            settings,
            payload,
            timeout=settings.openrouter_timeout_sec,
            max_context_tokens=settings.chat_max_context_tokens_est,
            char_per_token=settings.chat_char_per_token_est,
            http_client=http_client,
            stream_callback=stream_callback,
            models=text_models_for_tariff(settings, tariff),
        )
    except Exception:
        logger.exception("run_chat_turn: OpenRouter failed user_id=%s", user_id)
        await dialog_pop_last_for_user(user_id)
        await update_balance(user_id, spent_field, 1)
        await rollback_last(settings, user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.AI_FAILED)

    ans_trim = ans[: min(settings.chat_max_message_chars, 4090)]
    if text_role == "standard" and tariff.value == "free" and len(ans_trim) > 700:
        motivation = "\n\nНужен глубокий анализ или другой стиль? Загляни в «🚀 Тарифы» в меню бота."
        if motivation not in ans_trim and len(ans_trim) + len(motivation) <= 4090:
            ans_trim = f"{ans_trim}{motivation}"
    await commit_assistant_turn_queued(user_id, ans_trim, settings.dialog_prune_keep)
    conv.schedule_memory_refresh(settings, user_id)
    return ChatTurnResult(outcome=ChatTurnOutcome.SUCCESS, assistant_message=ans_trim)
