"""Юнит-тесты обрезки контекста перед OpenRouter."""

from __future__ import annotations

from services.context_pruning import prune_context_messages


def _count_tokens(msgs: list) -> int:
    total = 0
    for m in msgs:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
    return total


def test_prune_context_limits_message_count() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "1"},
        {"role": "assistant", "content": "2"},
        {"role": "user", "content": "3"},
        {"role": "assistant", "content": "4"},
    ]
    out, ok = prune_context_messages(messages, max_messages=2)
    assert ok is True
    assert len(out) == 3
    assert out[0]["content"] == "sys"
    assert out[1]["content"] == "3"
    assert out[2]["content"] == "4"


def test_prune_context_token_trim_keeps_system() -> None:
    messages = [
        {"role": "system", "content": "x" * 10},
        {"role": "user", "content": "a" * 50},
        {"role": "assistant", "content": "b" * 50},
        {"role": "user", "content": "c" * 50},
    ]
    out, ok = prune_context_messages(
        messages,
        max_tokens_est=120,
        estimate_tokens=_count_tokens,
    )
    assert ok is True
    assert out[0]["role"] == "system"
    assert _count_tokens(out) <= 120


def test_prune_context_returns_false_when_system_plus_one_user_too_large() -> None:
    messages = [
        {"role": "system", "content": "s" * 100},
        {"role": "user", "content": "u" * 100},
    ]
    out, ok = prune_context_messages(
        messages,
        max_tokens_est=50,
        estimate_tokens=_count_tokens,
    )
    assert ok is False
    assert len(out) == 2


def test_prune_context_empty_messages() -> None:
    out, ok = prune_context_messages([])
    assert out == []
    assert ok is True
