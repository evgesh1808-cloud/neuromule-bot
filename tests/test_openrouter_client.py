"""Тесты Context Caching обёртки OpenRouter (Маржа +95% Booster)."""

from __future__ import annotations

import pytest

from services.openrouter_client import (
    CACHE_FRIENDLY_MODELS,
    build_cache_friendly_messages,
    is_cache_friendly_model,
)


# ─── is_cache_friendly_model ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_id, expected",
    [
        ("google/gemini-2.5-pro", True),
        ("google/gemini-2.0-flash-lite:free", True),
        ("anthropic/claude-3.5-sonnet", True),
        ("anthropic/claude-3.7-haiku", True),
        ("openai/o1-mini", True),
        ("openai/o4-mini-high", True),
        ("openai/gpt-4o", True),
        ("openrouter/auto", False),
        ("meta-llama/llama-3.1-8b", False),
        ("", False),
        ("   ", False),
    ],
)
def test_is_cache_friendly_model_matrix(model_id: str, expected: bool) -> None:
    assert is_cache_friendly_model(model_id) is expected


def test_cache_friendly_models_list_is_non_empty() -> None:
    assert len(CACHE_FRIENDLY_MODELS) >= 3
    assert any(p.startswith("google/") for p in CACHE_FRIENDLY_MODELS)
    assert any(p.startswith("anthropic/") for p in CACHE_FRIENDLY_MODELS)


# ─── build_cache_friendly_messages ─────────────────────────────────────────


def test_build_messages_basic_order() -> None:
    out = build_cache_friendly_messages(
        system_prompt="Ты — NeuroMule.",
        persistent_memory=None,
        history=[],
        user_query="Привет",
    )
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[0]["content"] == "Ты — NeuroMule."
    assert out[-1]["content"] == "Привет"


def test_build_messages_inserts_memory_after_system() -> None:
    out = build_cache_friendly_messages(
        system_prompt="Ты — NeuroMule.",
        persistent_memory="Меня зовут Женя, я разраб.",
        history=[],
        user_query="Что мне попробовать?",
    )
    assert [m["role"] for m in out] == ["system", "system", "user"]
    mem = out[1]["content"]
    assert mem.startswith("[Данные о пользователе, учитывай при ответе:")
    assert "Меня зовут Женя, я разраб." in mem


def test_build_messages_preserves_history_order() -> None:
    history = [
        {"role": "user", "content": "первое"},
        {"role": "assistant", "content": "ответ 1"},
        {"role": "user", "content": "второе"},
        {"role": "assistant", "content": "ответ 2"},
    ]
    out = build_cache_friendly_messages(
        system_prompt="Sys.",
        persistent_memory=None,
        history=history,
        user_query="третье",
    )
    assert [m["role"] for m in out] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert out[1]["content"] == "первое"
    assert out[-1]["content"] == "третье"


def test_build_messages_drops_invalid_roles_and_empty() -> None:
    history = [
        {"role": "system", "content": "should-be-dropped"},  # роль не user/assistant
        {"role": "user", "content": "  "},  # пустой контент
        {"role": "USER", "content": "case-insensitive ok"},
        {"role": "assistant", "content": "ответ"},
    ]
    out = build_cache_friendly_messages(
        system_prompt="Sys.",
        persistent_memory=None,
        history=history,
        user_query="последний",
    )
    contents = [m["content"] for m in out]
    assert "should-be-dropped" not in contents
    assert "case-insensitive ok" in contents
    assert "ответ" in contents


def test_build_messages_is_stable_for_cache() -> None:
    """Ключевое свойство: одинаковый ввод → побайтово идентичный payload."""
    args = dict(
        system_prompt="Sys.",
        persistent_memory="user memory",
        history=[{"role": "user", "content": "hello"}],
        user_query="now what?",
    )
    a = build_cache_friendly_messages(**args)  # type: ignore[arg-type]
    b = build_cache_friendly_messages(**args)  # type: ignore[arg-type]
    assert a == b


def test_build_messages_skips_empty_persistent_memory() -> None:
    out = build_cache_friendly_messages(
        system_prompt="Sys.",
        persistent_memory="   ",
        history=[],
        user_query="hello",
    )
    assert [m["role"] for m in out] == ["system", "user"]


def test_build_messages_skips_empty_system_prompt() -> None:
    out = build_cache_friendly_messages(
        system_prompt="",
        persistent_memory=None,
        history=[],
        user_query="hello",
    )
    assert [m["role"] for m in out] == ["user"]
    assert out[0]["content"] == "hello"


def test_build_messages_ignores_non_dict_history_items() -> None:
    out = build_cache_friendly_messages(
        system_prompt="Sys.",
        persistent_memory=None,
        history=["not-a-dict", None, 42],  # type: ignore[list-item]
        user_query="hi",
    )
    assert [m["role"] for m in out] == ["system", "user"]
