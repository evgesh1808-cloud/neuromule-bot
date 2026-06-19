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
    # В логе явное упоминание 429 / rate_limited.
    rec = [r.message for r in caplog.records]
    assert any("429" in m or "rate_limited" in m for m in rec)
