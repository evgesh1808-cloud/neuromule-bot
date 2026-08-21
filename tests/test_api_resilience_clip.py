"""Защита от Telegram «message is too long» при fail generation."""

from __future__ import annotations

from services.api_resilience import ExternalApiError, clip_error_text, clip_telegram_text


def test_clip_error_text_truncates() -> None:
    huge = "x" * 10_000
    out = clip_error_text(huge, limit=200)
    assert len(out) <= 200
    assert out.endswith("...")


def test_external_api_error_clips_on_init() -> None:
    exc = ExternalApiError("Gemini", "A" * 5000)
    assert len(str(exc)) <= 200


def test_is_provider_quota_error_detects_openrouter_402() -> None:
    from services.api_resilience import is_provider_quota_error

    assert is_provider_quota_error(ExternalApiError("OpenRouter", "HTTP 402: insufficient credits"))
    assert is_provider_quota_error(ExternalApiError("Gemini", "quota exceeded"))
    assert not is_provider_quota_error(ExternalApiError("OpenRouter", "HTTP 500: internal"))


def test_clip_telegram_text_under_limit() -> None:
    assert len(clip_telegram_text("ok")) == 2
    huge = "ж" * 5000
    assert len(clip_telegram_text(huge)) <= 3900
