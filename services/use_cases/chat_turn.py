"""
Use-case: один полный цикл «пользователь написал в свободный чат → ответ нейросети».

Сюда вынесены правила списания энергии, rate limit (БД / Redis), запись истории, вызов OpenRouter
и откаты при ошибке. Слой platforms (Telegram) только показывает результат пользователю.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from config import Settings
from services import conversation as conv
from services import metrics
from services.ai_text import StreamCallback, ask_ai_messages, estimate_messages_prompt_tokens
from services.dialog_sanitize import compact_table_history_from_json
from services.dialog_write_worker import commit_assistant_turn_queued
from services.dialog_platform import DEFAULT_DIALOG_PLATFORM
from services.neurotext_media import build_openrouter_user_content
from services.rate_limit_service import allow_request, rollback_last
from services.billing import billing
from services.billing.chat_pipeline import prepare_openrouter_chat_messages
from services.billing.store import refund_charge
from services.context_pruning import prune_context_messages
from services.repository import dialog_append, dialog_pop_last_for_user, insert_table_report
from services.table_json import canonicalize_table_json
from services.table_text_response import extract_table_ai_insights
from services.telegram_safe_text import (
    collapse_excessive_line_breaks,
    markdown_tables_to_telegram_html,
    markdown_to_html,
    normalize_telegram_list_markup,
    repair_telegram_html,
)

logger = logging.getLogger(__name__)

# Лимит финансового отчёта WB/Ozon в Telegram (ручная загрузка xlsx).
WB_FINANCE_TELEGRAM_MAX_CHARS = 2000


def build_wb_finance_openrouter_prompts(
    matrix_rows: list[list[str]],
    *,
    revenue_total: float,
) -> tuple[str, str] | None:
    """
    System + user prompt для финансовой сессии после локального ETL.

    ABC, FOMO логистики невыкупов и OOS считаются в :mod:`services.file_processor`.
    """
    from services.table_wb_finance_ai import build_wb_finance_openrouter_prompt_pair

    return build_wb_finance_openrouter_prompt_pair(matrix_rows, revenue_total=revenue_total)


def strip_redacted_thinking(text: str) -> str:
    """Удаляет служебные блоки рассуждений модели перед показом пользователю."""
    if not text:
        return text
    flags = re.IGNORECASE | re.DOTALL
    text = re.sub(r"<think>.*?</think>", "", text, flags=flags)
    text = re.sub(r"<think>.*\Z", "", text, flags=flags)
    return text.strip()


def clean_markdown_to_html(text: str) -> str:
    """Обёртка для ответов чата: thinking → markdown → нормализация верстки → починка HTML."""
    text = strip_redacted_thinking(text)
    text = markdown_to_html(text)
    text = normalize_telegram_list_markup(text)
    text = collapse_excessive_line_breaks(text)
    return repair_telegram_html(text)


def format_assistant_for_role(text: str, text_role: str, *, for_stream: bool = False) -> str:
    """Финальная вёрстка ответа с учётом роли Нейротекста."""
    role_id = (text_role or "standard").strip().lower()
    if role_id == "table_generator":
        converted = markdown_tables_to_telegram_html(strip_redacted_thinking(text))
        return repair_telegram_html(converted)
    if role_id in ("blogger_content", "blogger"):
        from services.blogger_post_parser import (
            BloggerPostParsed,
            normalize_blogger_raw_output,
            reassemble_blogger_sections,
        )

        sections = normalize_blogger_raw_output(strip_redacted_thinking(text))
        display_plain = BloggerPostParsed(sections=sections).display_plain()
        fallback_text = reassemble_blogger_sections(sections)
        result = clean_markdown_to_html(display_plain or fallback_text)
    else:
        result = clean_markdown_to_html(text)
    if for_stream:
        # Автозакрытие <b>/<i>/<code>/<pre> в частичных SSE-чанках — без BadRequest.
        result = repair_telegram_html(result)
    return result


class ChatTurnOutcome(str, Enum):
    """Результат попытки обработать одно пользовательское сообщение в чате."""

    SUCCESS = "success"
    EMPTY_INPUT = "empty_input"
    RATE_LIMITED = "rate_limited"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    ROLE_NOT_ALLOWED = "role_not_allowed"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    CONTEXT_TOO_LARGE = "context_too_large"
    AI_FAILED = "ai_failed"
    TABLE_JSON_INVALID = "table_json_invalid"


@dataclass(frozen=True)
class ChatTurnResult:
    """
    Результат use-case ``run_chat_turn``.

    Поля:
        outcome — итоговый статус (см. ``ChatTurnOutcome``).
        assistant_message — текст ответа модели; заполнено только при ``SUCCESS``.
        table_raw_json — канонический JSON таблицы для Excel/Mini App API.
        table_report_id — id в ``table_reports`` для ``GET /api/v1/reports/{id}``.
        table_ai_insights — бизнес-выводы модели вне JSON (для «Один экран»).
    """

    outcome: ChatTurnOutcome
    assistant_message: str | None = None
    user_notice: str | None = None
    effective_text_role: str | None = None
    table_raw_json: str | None = None
    table_report_id: int | None = None
    table_ai_insights: str | None = None
    table_seo_xlsx_bytes: bytes | None = None
    table_worker: object | None = None  # ``TableWorkerResult`` при локальной сборке
    table_degraded: bool = False
    table_degradation_notice: str | None = None
    blogger_post_raw: str | None = None


def _apply_user_content_override(
    payload: list[dict[str, Any]],
    content: str | list[dict[str, Any]],
) -> None:
    """Подменяет content последней user-реплики (текст или multimodal)."""
    for i in range(len(payload) - 1, -1, -1):
        if payload[i].get("role") == "user":
            payload[i]["content"] = content
            return


def _record_openrouter_usage(
    *,
    user_id: int,
    model_id: str,
    role: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Фиксирует фактический расход токенов OpenRouter (метрики + лог для аналитики)."""
    labels = {"model": model_id, "role": role}
    metrics.observe("openrouter.prompt_tokens", float(prompt_tokens), labels)
    metrics.observe("openrouter.completion_tokens", float(completion_tokens), labels)
    if prompt_tokens or completion_tokens:
        logger.info(
            "run_chat_turn: openrouter usage user_id=%s role=%s model=%s "
            "prompt_tokens=%s completion_tokens=%s",
            user_id,
            role,
            model_id,
            prompt_tokens,
            completion_tokens,
        )


def _record_chat_success_billing(
    *,
    role: str,
    energy_cost: int,
    crystal_cost: int,
) -> None:
    """Успешная генерация и списание валюты — для /admin_stats."""
    labels = {"role": role}
    metrics.incr("chat.success", labels)
    if energy_cost > 0:
        metrics.incr("billing.spent_energy", labels, value=energy_cost)
    if crystal_cost > 0:
        metrics.incr("billing.spent_crystals", labels, value=crystal_cost)


async def run_chat_turn(
    settings: Settings,
    user_id: int,
    raw_user_text: str,
    *,
    dialog_user_text: str | None = None,
    user_image_data_url: str | None = None,
    send_typing: Callable[[], Awaitable[None]] | None = None,
    http_client: object | None = None,
    stream_callback: StreamCallback | None = None,
    text_role: str = "standard",
    platform: str = DEFAULT_DIALOG_PLATFORM,
) -> ChatTurnResult:
    """
    Выполняет один «ход» чата с нейросетью (без отправки сообщений в Telegram).

    Вход:
        settings — конфиг приложения.
        user_id — Telegram user id.
        raw_user_text — текст для модели (может включать контекст цитаты).
        dialog_user_text — если задан, в БД пишется он; иначе — ``raw_user_text``.
        send_typing — необязательный колбэк «показать typing» перед долгим запросом к API.
        http_client — опциональный ``httpx.AsyncClient`` (тесты).
        stream_callback — если задан, запрос к модели идёт в SSE-режиме (для live-редактирования в Telegram).

    Возвращает:
        ``ChatTurnResult`` с ``outcome`` и при успехе — ``assistant_message``.

    Побочные эффекты:
        при SUCCESS — в БД добавлены user+assistant, обрезана история, запланировано обновление памяти;
        при INSUFFICIENT / RATE_LIMITED / AI_FAILED — энергия и история согласованы (откаты где нужно).
    """
    if not (raw_user_text or "").strip() and not user_image_data_url:
        return ChatTurnResult(outcome=ChatTurnOutcome.EMPTY_INPUT)

    history_text = dialog_user_text if dialog_user_text is not None else raw_user_text
    if not (history_text or "").strip():
        history_text = "[📷 Фото]"

    if not await allow_request(settings, user_id, settings.chat_rate_limit_per_minute):
        return ChatTurnResult(outcome=ChatTurnOutcome.RATE_LIMITED)

    billing_result = await billing.resolve_and_charge_text_chat(user_id, text_role)
    plan = billing_result.plan
    charge_id = billing_result.charge_id
    effective_role = billing_result.effective_role_id

    if plan.blocked:
        await rollback_last(settings, user_id)
        if plan.block_reason == "expert_role_requires_paid_tariff":
            return ChatTurnResult(outcome=ChatTurnOutcome.ROLE_NOT_ALLOWED)
        if plan.block_reason == "role_requires_smart_tariff":
            return ChatTurnResult(outcome=ChatTurnOutcome.ROLE_NOT_ALLOWED)
        return ChatTurnResult(outcome=ChatTurnOutcome.INSUFFICIENT_BALANCE)

    await dialog_append(user_id, "user", history_text, platform=platform)
    payload = await conv.build_openrouter_messages(
        settings,
        user_id,
        effective_role,
        premium=plan.use_premium_prompt,
        platform=platform,
    )
    user_content = build_openrouter_user_content(
        raw_user_text,
        image_data_url=user_image_data_url,
    )
    if user_image_data_url or (dialog_user_text is not None and history_text != raw_user_text):
        _apply_user_content_override(payload, user_content)

    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=plan.use_premium_prompt,
        text_role=effective_role,
    )

    def _estimate_payload_tokens(msgs: list) -> int:
        return estimate_messages_prompt_tokens(msgs, settings=settings)

    payload, fits_limit = prune_context_messages(
        payload,
        max_messages=settings.chat_history_limit,
        max_tokens_est=settings.chat_max_context_tokens_est,
        estimate_tokens=_estimate_payload_tokens,
    )
    if not fits_limit:
        await dialog_pop_last_for_user(user_id, platform=platform)
        if charge_id:
            await refund_charge(charge_id)
        await rollback_last(settings, user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.CONTEXT_TOO_LARGE)

    if send_typing is not None:
        try:
            await send_typing()
        except Exception:
            logger.debug("send_typing failed", exc_info=True)

    is_table_role = (effective_role or "").strip().lower() == "table_generator"
    if is_table_role:
        stream_callback = None

    # Основная модель из биллинга + резервный каскад (FREE/MINI → free_models, SMART/ULTRA → smart_models).
    model_chain: list[str] = []
    for mid in (plan.model_id, *getattr(plan, "fallback_model_ids", ())):
        mid = str(mid).strip()
        if mid and mid not in model_chain:
            model_chain.append(mid)

    safe_stream_callback: StreamCallback | None = None
    if stream_callback is not None:
        stream_fn = getattr(stream_callback, "on_stream", stream_callback)

        async def _safe_stream_callback(full_text: str, done: bool) -> None:
            try:
                await stream_fn(
                    format_assistant_for_role(full_text, effective_role, for_stream=True),
                    done,
                )
            except Exception:
                # Битый HTML в стриме не должен валить весь ход чата.
                logger.debug("stream_callback failed (ignored)", exc_info=True)
        safe_stream_callback = _safe_stream_callback

    try:
        completion = await ask_ai_messages(
            settings,
            payload,
            timeout=settings.openrouter_timeout_sec,
            max_context_tokens=settings.chat_max_context_tokens_est,
            char_per_token=settings.chat_char_per_token_est,
            http_client=http_client,
            stream_callback=safe_stream_callback,
            models=model_chain,
            max_tokens=plan.max_tokens,
            text_role=effective_role,
        )
    except Exception:
        logger.exception("run_chat_turn: OpenRouter failed user_id=%s", user_id)
        await dialog_pop_last_for_user(user_id, platform=platform)
        if charge_id:
            await refund_charge(charge_id)
        await rollback_last(settings, user_id)
        return ChatTurnResult(outcome=ChatTurnOutcome.AI_FAILED)

    content = completion.get("content") or ""
    try:
        prompt_tokens = int(completion.get("prompt_tokens") or 0)
        completion_tokens = int(completion.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        prompt_tokens = 0
        completion_tokens = 0

    _record_openrouter_usage(
        user_id=user_id,
        model_id=plan.model_id,
        role=effective_role,
        prompt_tokens=max(prompt_tokens, 0),
        completion_tokens=max(completion_tokens, 0),
    )

    raw_answer = strip_redacted_thinking(content)
    is_table_role = (effective_role or "").strip().lower() == "table_generator"

    if is_table_role:
        try:
            table_json = canonicalize_table_json(raw_answer)
            if not table_json:
                raise ValueError("invalid or empty table JSON")

            ai_insights = extract_table_ai_insights(raw_answer)
            await commit_assistant_turn_queued(
                user_id,
                compact_table_history_from_json(table_json, table_subrole=effective_role),
                settings.dialog_prune_keep,
                platform=platform,
            )
            report_id = await insert_table_report(user_id, table_json)
            conv.schedule_memory_refresh(settings, user_id, platform=platform)
            _record_chat_success_billing(
                role=effective_role,
                energy_cost=plan.energy_cost,
                crystal_cost=plan.crystal_cost,
            )
            return ChatTurnResult(
                outcome=ChatTurnOutcome.SUCCESS,
                assistant_message=None,
                user_notice=billing_result.notice,
                effective_text_role=effective_role,
                table_raw_json=table_json,
                table_report_id=report_id,
                table_ai_insights=ai_insights,
            )
        except Exception:
            logger.warning(
                "run_chat_turn: table JSON pipeline failed user_id=%s raw=%s",
                user_id,
                raw_answer[:500],
                exc_info=True,
            )
            await dialog_pop_last_for_user(user_id, platform=platform)
            if charge_id:
                await refund_charge(charge_id)
            await rollback_last(settings, user_id)
            return ChatTurnResult(outcome=ChatTurnOutcome.TABLE_JSON_INVALID)

    blogger_post_raw: str | None = None
    content_for_format = content
    if (effective_role or "").strip().lower() in ("blogger_content", "blogger"):
        from services.blogger_post_parser import (
            normalize_blogger_raw_output,
            reassemble_blogger_sections,
        )

        blogger_sections = normalize_blogger_raw_output(content)
        blogger_post_raw = reassemble_blogger_sections(blogger_sections)
        content_for_format = blogger_post_raw

    ans_trim = format_assistant_for_role(content_for_format, effective_role)
    if plan.max_tokens <= 1000:
        ans_trim = ans_trim[: min(settings.chat_max_message_chars, 4090)]
    else:
        ans_trim = ans_trim[: settings.chat_max_message_chars]
    await commit_assistant_turn_queued(user_id, ans_trim, settings.dialog_prune_keep, platform=platform)
    conv.schedule_memory_refresh(settings, user_id, platform=platform)
    _record_chat_success_billing(
        role=effective_role,
        energy_cost=plan.energy_cost,
        crystal_cost=plan.crystal_cost,
    )
    return ChatTurnResult(
        outcome=ChatTurnOutcome.SUCCESS,
        assistant_message=ans_trim,
        user_notice=billing_result.notice,
        effective_text_role=effective_role,
        blogger_post_raw=blogger_post_raw,
    )
