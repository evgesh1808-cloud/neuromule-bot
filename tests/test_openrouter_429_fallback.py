"""Тест: 429 (rate-limit) → возврат ``None`` → внешний цикл переключается."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import services.ai_text as ai_text


class _StubResponse:
    def __init__(self, status_code: int, body: str = "rate limited") -> None:
        self.status_code = status_code
        self.text = body

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok"}}]}


class _StubClient:
    """Мини-httpx клиент: первая модель → 429, вторая → 200."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url: str, *, headers, json, timeout):
        self.calls.append((url, json))
        model = json["model"]
        if model == "free-model-a":
            return _StubResponse(429, "{'error': 'rate'}")
        return _StubResponse(200, "ok")


@pytest.mark.asyncio
async def test_429_logs_warning_and_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="services.ai_text")
    settings = SimpleNamespace(
        openrouter_chat_url="https://x",
        openrouter_key="k",
        bot_name="NeuroMule",
        openrouter_timeout_sec=10,
        openrouter_max_output_tokens=512,
    )

    # Прямой вызов _post_chat_completion при status=429 → None.
    client = _StubClient()
    out = await ai_text._post_chat_completion(
        client,  # type: ignore[arg-type]
        settings,
        "free-model-a",
        [{"role": "user", "content": "hi"}],
        timeout=10.0,
    )
    assert out is None
    # В логе явное упоминание 429 / недоступна.
    rec = [r.message for r in caplog.records]
    assert any("429" in m for m in rec)


@pytest.mark.asyncio
async def test_429_log_includes_next_model_and_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="services.ai_text")
    settings = SimpleNamespace(
        openrouter_chat_url="https://x",
        openrouter_key="k",
        bot_name="NeuroMule",
        openrouter_timeout_sec=10,
        openrouter_max_output_tokens=512,
    )
    client = _StubClient()
    await ai_text._post_chat_completion(
        client,  # type: ignore[arg-type]
        settings,
        "free-model-a",
        [{"role": "user", "content": "hi"}],
        timeout=10.0,
        log_context="hd_genetic_synthesis",
        next_model="deepseek/deepseek-r1",
    )
    joined = " ".join(r.message for r in caplog.records)
    assert "deepseek/deepseek-r1" in joined
    assert "hd_genetic_synthesis" in joined


@pytest.mark.asyncio
async def test_404_and_503_failover_to_next_model() -> None:
    settings = SimpleNamespace(
        openrouter_chat_url="https://x",
        openrouter_key="k",
        bot_name="NeuroMule",
        openrouter_timeout_sec=12,
        openrouter_max_output_tokens=512,
        free_models=["a", "b"],
        chat_char_per_token_est=3,
    )

    class _Client:
        def __init__(self) -> None:
            self.models: list[str] = []

        async def post(self, url: str, *, headers, json, timeout):
            self.models.append(json["model"])
            if json["model"] == "gone:free":
                return _StubResponse(404)
            if json["model"] == "busy:free":
                return _StubResponse(503)
            return _StubResponse(200)

    client = _Client()
    out = await ai_text.ask_ai_messages(
        settings,  # type: ignore[arg-type]
        [{"role": "user", "content": "hi"}],
        models=["gone:free", "busy:free", "ok:free"],
        http_client=client,  # type: ignore[arg-type]
        timeout=12.0,
    )
    assert out["content"] == "ok"
    assert client.models == ["gone:free", "busy:free", "ok:free"]


def test_httpx_timeout_uses_fast_connect() -> None:
    t = ai_text._httpx_timeout(12.0)
    assert t.connect == 3.0
    assert float(t.read) == 12.0
