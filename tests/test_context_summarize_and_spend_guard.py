"""Тесты context summarize + API spend guard (без wiring в chat_turn)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from config import settings as app_settings
from services.billing.api_spend_guard import (
    check_free_daily_chat,
    check_global_usd_cap,
    check_user_daily_tokens,
    consume_free_daily_chat,
    daily_token_cap_for_tariff,
    preflight_spend,
    record_token_usage,
    reset as reset_spend,
    snapshot,
)
from services.billing.types import TariffTier
from services.context_summarize import (
    DIALOG_SUMMARY_MARKER,
    maybe_compact_messages,
    role_allows_dialog_summary,
    split_head_and_tail,
)


@pytest.fixture(autouse=True)
def _clean_spend_guard() -> None:
    reset_spend()
    yield
    reset_spend()


def test_role_allows_dialog_summary_skips_table_allows_standard() -> None:
    assert role_allows_dialog_summary("standard") is True
    assert role_allows_dialog_summary("table_generator") is False
    assert role_allows_dialog_summary("blogger_content") is True
    assert role_allows_dialog_summary("psychologist_coach") is True
    assert role_allows_dialog_summary("") is False


def test_split_head_and_tail_keeps_last_pairs() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]
    head, tail = split_head_and_tail(messages, keep_pairs=2)
    assert [m["content"] for m in head] == ["u1", "a1"]
    assert [m["content"] for m in tail] == ["u2", "a2", "u3", "a3"]
    assert all(m.get("role") != "system" for m in head + tail)


@pytest.mark.asyncio
async def test_maybe_compact_summarizes_when_over_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = app_settings.model_copy(
        update={
            "chat_summary_keep_pairs": 1,
            "chat_summary_max_chars": 700,
            "chat_summary_model": "google/gemini-2.5-flash-lite",
            "chat_summary_timeout_sec": 5.0,
        }
    )
    monkeypatch.setattr("services.context_summarize.settings", patched)

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "старое1"},
        {"role": "assistant", "content": "ответ1"},
        {"role": "user", "content": "старое2"},
        {"role": "assistant", "content": "ответ2"},
        {"role": "user", "content": "сейчас"},
    ]

    async def _ask(msgs: list[dict[str, Any]], **_kwargs: Any) -> dict[str, str]:
        assert msgs[0]["role"] == "system"
        return {"content": "Пользователь обсуждал старое1/старое2."}

    with patch(
        "services.context_summarize.estimate_messages_prompt_tokens",
        return_value=9_000,
    ):
        out, summarized = await maybe_compact_messages(
            messages,
            trigger_tokens=8_000,
            ask_fn=_ask,
        )

    assert summarized is True
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "assistant"
    assert DIALOG_SUMMARY_MARKER in out[1]["content"]
    assert out[-1]["content"] == "сейчас"
    assert "старое1" not in str(out[2:])


@pytest.mark.asyncio
async def test_maybe_compact_fail_open_on_llm_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = app_settings.model_copy(
        update={
            "chat_summary_keep_pairs": 1,
            "chat_summary_timeout_sec": 2.0,
        }
    )
    monkeypatch.setattr("services.context_summarize.settings", patched)

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "now"},
    ]

    async def _boom(*_a: Any, **_k: Any) -> dict[str, str]:
        raise RuntimeError("provider down")

    with patch(
        "services.context_summarize.estimate_messages_prompt_tokens",
        return_value=99_000,
    ):
        out, summarized = await maybe_compact_messages(
            messages,
            trigger_tokens=100,
            ask_fn=_boom,
        )

    assert summarized is False
    assert out is messages
    assert len(out) == 4

    async def _hang(*_a: Any, **_k: Any) -> dict[str, str]:
        import asyncio

        await asyncio.sleep(60)
        return {"content": "late"}

    monkeypatch.setattr(
        "services.context_summarize.settings",
        app_settings.model_copy(
            update={
                "chat_summary_keep_pairs": 1,
                "chat_summary_timeout_sec": 0.05,
            }
        ),
    )
    with patch(
        "services.context_summarize.estimate_messages_prompt_tokens",
        return_value=99_000,
    ):
        out_t, summarized_t = await maybe_compact_messages(
            list(messages),
            trigger_tokens=100,
            ask_fn=_hang,
        )
    assert summarized_t is False
    assert len(out_t) == 4


def test_free_daily_chat_limit_enforced() -> None:
    day = "2099-01-01"
    assert check_free_daily_chat(
        42, tariff=TariffTier.FREE, limit=2, enforce=True, day=day
    ).ok
    consume_free_daily_chat(42, day=day)
    consume_free_daily_chat(42, day=day)
    blocked = check_free_daily_chat(
        42, tariff=TariffTier.FREE, limit=2, enforce=True, day=day
    )
    assert blocked.ok is False
    assert blocked.reason == "free_daily_chat_limit"
    assert check_free_daily_chat(
        42, tariff=TariffTier.MINI, limit=2, enforce=True, day=day
    ).ok


def test_user_daily_tokens_cap_blocks() -> None:
    day = "2099-02-02"
    record_token_usage(7, tokens_used=60_000, cost_usd=0.0, day=day)
    blocked = check_user_daily_tokens(7, projected_tokens=30_000, cap=80_000, day=day)
    assert blocked.ok is False
    assert blocked.reason == "user_daily_tokens_cap"
    assert check_user_daily_tokens(7, projected_tokens=1_000_000, cap=0, day=day).ok


def test_global_usd_cap_blocks() -> None:
    day = "2099-02-03"
    record_token_usage(9, tokens_used=100, cost_usd=0.4, day=day)
    blocked = check_global_usd_cap(projected_usd=0.2, cap_usd=0.5, day=day)
    assert blocked.ok is False
    assert blocked.reason == "global_usd_cap"
    assert check_global_usd_cap(projected_usd=0.05, cap_usd=0.5, day=day).ok
    assert check_global_usd_cap(projected_usd=99.0, cap_usd=0.0, day=day).ok


def test_preflight_and_tariff_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = app_settings.model_copy(
        update={
            "user_daily_tokens_cap_free": 10,
            "user_daily_tokens_cap_mini": 20,
            "user_daily_tokens_cap_smart": 30,
            "user_daily_tokens_cap_ultra": 40,
            "free_daily_chat_limit": 1,
            "free_daily_chat_enforce": True,
            "openrouter_daily_usd_cap": 0.0,
        }
    )
    monkeypatch.setattr("services.billing.api_spend_guard.settings", patched)

    assert daily_token_cap_for_tariff(TariffTier.FREE) == 10
    assert daily_token_cap_for_tariff(TariffTier.MINI) == 20
    assert daily_token_cap_for_tariff(TariffTier.SMART) == 30
    assert daily_token_cap_for_tariff(TariffTier.ULTRA) == 40

    day = "2099-03-03"
    ok = preflight_spend(1, TariffTier.FREE, projected_tokens=5, day=day)
    assert ok.ok

    consume_free_daily_chat(1, day=day)
    blocked_free = preflight_spend(1, TariffTier.FREE, projected_tokens=5, day=day)
    assert blocked_free.ok is False
    assert blocked_free.reason == "free_daily_chat_limit"

    ok_smart = preflight_spend(2, TariffTier.SMART, projected_tokens=10, day=day)
    assert ok_smart.ok
    record_token_usage(2, tokens_used=25, cost_usd=0.0, day=day)
    blocked_tok = preflight_spend(2, TariffTier.SMART, projected_tokens=10, day=day)
    assert blocked_tok.ok is False
    assert blocked_tok.reason == "user_daily_tokens_cap"

    snap = snapshot(day=day)
    assert snap["free_chats"][1] == 1
    assert snap["user_tokens"][2] == 25


@pytest.mark.asyncio
async def test_compact_standard_puts_followup_ref_in_system() -> None:
    from services.context_summarize import (
        FOLLOWUP_REF_MARKER,
        compact_standard_dialog_context,
    )

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Расскажи про тхэквондо для сына 7 лет"},
        {
            "role": "assistant",
            "content": "Тхэквондо развивает дисциплину и координацию. Секция 2 раза в неделю.",
        },
        {
            "role": "user",
            "content": (
                "Про сроки?\n\n[Compliance: PREMIUM COPY PACK]\n"
                "ТИП Б по умолчанию. Длинный хвост комплаенса…"
            ),
        },
    ]
    out = await compact_standard_dialog_context(messages, ask_fn=None)
    roles = [m["role"] for m in out]
    assert roles == ["system", "assistant", "user"]
    assert FOLLOWUP_REF_MARKER in out[0]["content"]
    assert "исходная тема" in out[0]["content"].lower()
    assert "тхэквондо" in out[0]["content"].lower()
    assert "Тхэквондо развивает" in out[1]["content"]
    assert "По теме" in out[2]["content"] or out[2]["content"].startswith("Про сроки?")
    assert "[Контекст:" in out[0]["content"]


@pytest.mark.asyncio
async def test_compact_standard_drops_assistant_for_long_user() -> None:
    from services.context_summarize import compact_standard_dialog_context

    long_q = "Объясни подробно " + ("историю тхэквондо " * 20)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "старый ответ"},
        {"role": "user", "content": long_q},
    ]
    out = await compact_standard_dialog_context(messages, ask_fn=None)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user"]
    assert "старый ответ" not in " ".join(m["content"] for m in out if m["role"] != "system")
