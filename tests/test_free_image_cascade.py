"""Тесты каскада FREE Nano Banana (строгий RR по 4 ключам)."""

from __future__ import annotations

import pytest

from services.api_resilience import ExternalApiError
from services.free_image_cascade import (
    FreeImageCascadeExhausted,
    OpenRouterPaidBlockedError,
    _extract_b64_from_payload,
    build_free_image_providers,
    ensure_openrouter_free_model,
    generate_free_tier_image,
    reset_free_image_rr_for_tests,
)


def test_extract_b64_from_openai_style_payload() -> None:
    import base64

    raw = b"fake-png"
    b64 = base64.b64encode(raw).decode()
    payload = {
        "choices": [
            {
                "message": {
                    "images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}],
                }
            }
        ]
    }
    out = _extract_b64_from_payload(payload)
    assert out == raw


def test_ensure_openrouter_free_model_appends_suffix() -> None:
    assert (
        ensure_openrouter_free_model("google/gemini-2.5-flash-image-preview")
        == "google/gemini-2.5-flash-image-preview:free"
    )


def test_ensure_openrouter_free_model_blocks_paid_variants() -> None:
    with pytest.raises(OpenRouterPaidBlockedError):
        ensure_openrouter_free_model("google/gemini-2.5-flash-image-preview:nitro")


def test_build_free_image_providers_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from config import settings

    object.__setattr__(settings, "gemini_api_key", "g1")
    object.__setattr__(settings, "gemini_api_key_2", "g2")
    object.__setattr__(settings, "openrouter_key", "o1")
    object.__setattr__(settings, "openrouter_key_2", "o2")
    slots = build_free_image_providers()
    assert [s["type"] for s in slots] == ["gemini", "gemini", "openrouter", "openrouter"]
    assert [s["key"] for s in slots] == ["g1", "g2", "o1", "o2"]


def test_build_free_image_providers_skips_empty() -> None:
    from config import settings

    object.__setattr__(settings, "gemini_api_key", "g1")
    object.__setattr__(settings, "gemini_api_key_2", "")
    object.__setattr__(settings, "openrouter_key", "o1")
    object.__setattr__(settings, "openrouter_key_2", "")
    slots = build_free_image_providers()
    assert len(slots) == 2
    assert slots[0]["key"] == "g1"
    assert slots[1]["key"] == "o1"


@pytest.mark.asyncio
async def test_cascade_exhausted_when_all_slots_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_free_image_rr_for_tests()
    object.__setattr__(
        __import__("config", fromlist=["settings"]).settings,
        "free_image_key_pause_sec",
        0.0,
    )

    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [
            {"type": "gemini", "key": "g1"},
            {"type": "openrouter", "key": "o1"},
        ],
    )

    async def _fail(*_a, **_k):
        raise ExternalApiError("Test", "HTTP 429")

    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _fail)

    with pytest.raises(FreeImageCascadeExhausted):
        await generate_free_tier_image("sunset")


@pytest.mark.asyncio
async def test_round_robin_advances_index(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.free_image_cascade as fic

    reset_free_image_rr_for_tests()
    object.__setattr__(
        __import__("config", fromlist=["settings"]).settings,
        "free_image_key_pause_sec",
        0.0,
    )
    seen: list[str] = []

    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [
            {"type": "gemini", "key": "g1"},
            {"type": "gemini", "key": "g2"},
            {"type": "openrouter", "key": "o1"},
            {"type": "openrouter", "key": "o2"},
        ],
    )

    async def _ok(slot, *_a, **_k):
        seen.append(slot["key"])
        from services.gemini_image_client import GeminiImageResult

        return GeminiImageResult(data=b"ok")

    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _ok)

    await generate_free_tier_image("a")
    await generate_free_tier_image("b")
    await generate_free_tier_image("c")
    assert seen == ["g1", "g2", "o1"]
    assert fic.global_provider_index == 3


@pytest.mark.asyncio
async def test_failover_on_429_shifts_within_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.free_image_cascade as fic
    from services.gemini_image_client import GeminiImageResult

    reset_free_image_rr_for_tests()
    object.__setattr__(
        __import__("config", fromlist=["settings"]).settings,
        "free_image_key_pause_sec",
        0.0,
    )
    seen: list[str] = []
    calls = {"n": 0}

    monkeypatch.setattr(
        "services.free_image_cascade.build_free_image_providers",
        lambda: [
            {"type": "gemini", "key": "g1"},
            {"type": "gemini", "key": "g2"},
            {"type": "openrouter", "key": "o1"},
            {"type": "openrouter", "key": "o2"},
        ],
    )

    async def _flaky(slot, *_a, **_k):
        seen.append(slot["key"])
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExternalApiError("Gemini", "HTTP 429")
        return GeminiImageResult(data=b"ok")

    monkeypatch.setattr("services.free_image_cascade._invoke_slot", _flaky)

    out = await generate_free_tier_image("x")
    assert out.data == b"ok"
    assert seen == ["g1", "g2"]
    # Два обращения к API → индекс сдвинулся на 2.
    assert fic.global_provider_index == 2
