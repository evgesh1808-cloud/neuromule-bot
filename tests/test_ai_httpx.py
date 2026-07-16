"""Мок httpx: ask_ai_messages / ask_ai_text."""

from __future__ import annotations

import httpx
import pytest
from config import Settings
from services.ai_text import ask_ai_messages, ask_ai_text


async def test_ask_ai_messages_success_mock():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ответ"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await ask_ai_messages(
            s,
            [{"role": "user", "content": "x"}],
            http_client=client,
        )
    assert out["content"] == "ответ"
    assert out["prompt_tokens"] == 12
    assert out["completion_tokens"] == 7


async def test_ask_ai_messages_missing_usage_defaults_to_zero():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await ask_ai_messages(
            s,
            [{"role": "user", "content": "x"}],
            http_client=client,
        )
    assert out["content"] == "ok"
    assert out["prompt_tokens"] == 0
    assert out["completion_tokens"] == 0


async def test_ask_ai_messages_stream_includes_usage_option():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = __import__("json").loads(request.content.decode())
        lines = [
            'data: {"choices":[{"delta":{"content":"a"}}]}\n\n',
            'data: {"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n',
            "data: [DONE]\n\n",
        ]
        return httpx.Response(200, content="".join(lines).encode())

    transport = httpx.MockTransport(handler)
    edits: list[str] = []

    async def stream_cb(text: str, done: bool) -> None:
        edits.append(text)

    async with httpx.AsyncClient(transport=transport) as client:
        out = await ask_ai_messages(
            s,
            [{"role": "user", "content": "x"}],
            http_client=client,
            stream_callback=stream_cb,
        )
    assert captured["json"]["stream"] is True
    assert captured["json"]["stream_options"] == {"include_usage": True}
    assert out["content"] == "a"
    assert out["prompt_tokens"] == 5
    assert out["completion_tokens"] == 2


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


async def test_ask_ai_messages_keeps_free_suffix_before_request():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})
    seen_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content.decode())
        seen_models.append(body["model"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await ask_ai_messages(
            s,
            [{"role": "user", "content": "x"}],
            models=[
                "meta-llama/llama-3.2-3b-instruct:free",
                "openrouter/free",
            ],
            http_client=client,
        )
    assert out["content"] == "ok"
    assert seen_models == ["meta-llama/llama-3.2-3b-instruct:free"]


async def test_ask_ai_text_maps_unavailable_to_user_string():
    s = Settings().model_copy(update={"free_models": ["m1"], "openrouter_key": "k"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="no")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        out = await ask_ai_text(s, "ping", http_client=client)

    assert "недоступен" in out.lower()


@pytest.mark.asyncio
async def test_ask_ai_messages_shared_client_scope_e2e(monkeypatch):
    """Сквозной путь: ``_http_client_scope`` + singleton без инжекта ``http_client``."""
    from services import openrouter_http as or_http

    await or_http.close_openrouter_http_client()

    requests_made: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "via-shared"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)
    s = Settings().model_copy(
        update={
            "free_models": ["m1"],
            "openrouter_key": "k",
            "ai_proxy": "http://127.0.0.1:7890",
        }
    )

    async def _init_mock_client(settings):
        await or_http.close_openrouter_http_client()
        kwargs = or_http.openrouter_client_kwargs(settings)
        kwargs.pop("proxy", None)
        or_http._shared_client = httpx.AsyncClient(transport=transport, **kwargs)
        return or_http._shared_client

    monkeypatch.setattr(or_http, "init_openrouter_http_client", _init_mock_client)
    monkeypatch.setattr(
        or_http,
        "get_openrouter_http_client",
        _init_mock_client,
    )

    out = await ask_ai_messages(s, [{"role": "user", "content": "x"}])
    assert out["content"] == "via-shared"
    assert requests_made
    kw = or_http.openrouter_client_kwargs(s)
    assert kw["trust_env"] is False
    assert kw["proxy"] == "http://127.0.0.1:7890"

    await or_http.close_openrouter_http_client()
