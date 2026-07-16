"""Suggested Replies для роли standard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services.standard_suggested_replies import (
    BUTTONS_MARKER,
    build_suggested_replies_keyboard,
    clear_suggested_replies_for_tests,
    parse_std_reply_callback,
    remember_suggested_replies,
    resolve_suggested_reply,
    split_suggested_replies,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_suggested_replies_for_tests()
    yield
    clear_suggested_replies_for_tests()


def test_split_suggested_replies_extracts_labels() -> None:
    raw = (
        "Короткий ответ про тему.\n\n"
        f"{BUTTONS_MARKER}\n"
        "Уточни сроки\n"
        "Какой бюджет\n"
        "Нужен пример\n"
        "лишняя строка не нужна\n"
    )
    body, labels = split_suggested_replies(raw)
    assert "Короткий ответ" in body
    assert BUTTONS_MARKER not in body
    assert labels == ["Уточни сроки", "Какой бюджет", "Нужен пример"]


def test_split_suggested_replies_without_marker() -> None:
    body, labels = split_suggested_replies("Просто текст")
    assert body == "Просто текст"
    assert labels == []


def test_remember_and_resolve_suggested_reply() -> None:
    cid = remember_suggested_replies(42, ["Первый вопрос", "Второй вопрос"])
    assert cid
    assert resolve_suggested_reply(cid, 0, user_id=42) == "Первый вопрос"
    assert resolve_suggested_reply(cid, 1, user_id=42) == "Второй вопрос"
    assert resolve_suggested_reply(cid, 0, user_id=99) is None
    assert resolve_suggested_reply("nope", 0, user_id=42) is None


def test_keyboard_callback_format() -> None:
    labels = ["А", "Б"]
    cid = remember_suggested_replies(7, labels)
    assert cid
    kb = build_suggested_replies_keyboard(cid, labels)
    assert kb is not None
    flat = [b for row in kb.inline_keyboard for b in row]
    assert flat[0].callback_data == f"{msg.CB_STD_REPLY_PREFIX}0:{cid}"
    assert parse_std_reply_callback(flat[1].callback_data) == (1, cid)


def test_role_standard_prompt_has_buttons_rule() -> None:
    from content.chat_prompt import _ROLE_STANDARD

    assert "===КНОПКИ===" in _ROLE_STANDARD
    assert "blockquote expandable" in _ROLE_STANDARD
    assert "Пример реплики" in _ROLE_STANDARD
    # Старая структура капс-секций больше не является обязательным форматом ответа
    assert "выдели структуру СТРОГО по блокам" not in _ROLE_STANDARD


@pytest.mark.asyncio
async def test_run_chat_turn_strips_buttons_into_suggested_replies() -> None:
    from services.billing.types import ChatRoutePlan, CurrencyKind, TextChatBillingResult
    from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn

    completion = {
        "content": (
            "Ответ модели.\n\n"
            f"{BUTTONS_MARKER}\n"
            "Следующий шаг\n"
            "Другой вопрос\n"
        ),
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    plan = ChatRoutePlan(
        model_id="google/gemini-2.5-flash",
        price_type=CurrencyKind.ENERGY,
        energy_cost=1,
        crystal_cost=1,
        is_expert_role=False,
        max_tokens=2000,
        use_premium_prompt=False,
        fallback_model_ids=(),
        blocked=False,
    )
    billing = TextChatBillingResult(
        effective_role_id="standard",
        plan=plan,
        charge_id="c1",
        notice=None,
    )

    with (
        patch("services.use_cases.chat_turn.allow_request", AsyncMock(return_value=True)),
        patch(
            "services.use_cases.chat_turn.billing.resolve_and_charge_text_chat",
            AsyncMock(return_value=billing),
        ),
        patch(
            "services.use_cases.chat_turn.conv.build_openrouter_messages",
            AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
        ),
        patch(
            "services.use_cases.chat_turn.prepare_openrouter_chat_messages",
            return_value=[{"role": "user", "content": "hi"}],
        ),
        patch(
            "services.use_cases.chat_turn.prune_context_messages",
            return_value=([{"role": "user", "content": "hi"}], True),
        ),
        patch(
            "services.use_cases.chat_turn.ask_ai_messages",
            AsyncMock(return_value=completion),
        ),
        patch("services.use_cases.chat_turn.commit_assistant_turn_queued", AsyncMock()),
        patch("services.use_cases.chat_turn.conv.schedule_memory_refresh"),
        patch("services.use_cases.chat_turn.dialog_append", AsyncMock()),
    ):
        from config import Settings

        result = await run_chat_turn(
            Settings(tg_token="t", openrouter_key="k"),
            1001,
            "вопрос",
            text_role="standard",
        )

    assert result.outcome is ChatTurnOutcome.SUCCESS
    assert result.assistant_message is not None
    assert BUTTONS_MARKER not in result.assistant_message
    assert "Следующий шаг" not in (result.assistant_message or "")
    assert result.suggested_replies == ("Следующий шаг", "Другой вопрос")
