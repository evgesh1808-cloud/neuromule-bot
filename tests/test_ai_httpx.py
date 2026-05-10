"""Мок httpx: ask_ai_messages / ask_ai_text."""

from __future__ import annotations

import httpx
from config import Settings
from services.ai_text import ask_ai_messages, ask_ai_text


async def test_ask_ai_messages_success_mock():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ответ"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await ask_ai_messages(
            s,
            [{"role": "user", "content": "x"}],
            http_client=client,
        )
    assert out == "ответ"


async def test_ask_ai_messages_token_limit_raises():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})
    messages = [{"role": "user", "content": "x" * 30}]

    try:
        await ask_ai_messages(
            s,
            messages,
            max_context_tokens=5,
            char_per_token=1,
        )
    except RuntimeError as e:
        assert str(e) == "context_too_long_tokens"
    else:
        raise AssertionError("expected RuntimeError")


async def test_ask_ai_text_maps_unavailable_to_user_string():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="no")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await ask_ai_text(s, "ping", http_client=client)

    assert "недоступен" in out.lower()
