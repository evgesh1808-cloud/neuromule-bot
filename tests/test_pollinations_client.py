"""Pollinations Flux Schnell + FREE-tier routing в generation_jobs."""

from __future__ import annotations

from urllib.parse import unquote

import pytest

from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.pollinations_client import build_pollinations_flux_url
from services import generation_jobs


def test_build_pollinations_flux_url_encodes_prompt() -> None:
    url = build_pollinations_flux_url("кот в космосе", api_key="")
    assert url.startswith("https://image.pollinations.ai/prompt/")
    assert "model=flux" in url
    assert "width=1024" in url
    assert "height=1024" in url
    assert "nologo=true" in url
    assert unquote(url.split("/prompt/", 1)[1].split("?", 1)[0])


def test_build_pollinations_flux_url_uses_gen_when_key() -> None:
    url = build_pollinations_flux_url("cat", api_key="sk_test")
    assert url.startswith("https://gen.pollinations.ai/image/")
    assert "key=sk_test" in url


def test_build_pollinations_flux_url_rejects_empty() -> None:
    with pytest.raises(ExternalApiError):
        build_pollinations_flux_url("   ")


@pytest.mark.asyncio
async def test_free_tier_flux_routes_to_pollinations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _fake_pollinations(prompt: str) -> GeminiImageResult:
        calls.append(prompt)
        return GeminiImageResult(data=b"webp-bytes")

    async def _fake_replicate(*_a, **_k):
        raise AssertionError("Replicate must not be called for FREE flux")

    monkeypatch.setattr(generation_jobs, "generate_flux_schnell_image", _fake_pollinations)
    monkeypatch.setattr(generation_jobs, "call_replicate_model", _fake_replicate)

    async def _fake_row(_uid: int):
        class _R:
            tariff = "free"

        return _R()

    monkeypatch.setattr(generation_jobs, "get_user_row", _fake_row)

    result = await generation_jobs._generate_photo_result(
        "flux_schnell",
        "sunset over mountains",
        user_id=42,
    )
    assert isinstance(result, GeminiImageResult)
    assert result.data == b"webp-bytes"
    assert calls == ["sunset over mountains"]


@pytest.mark.asyncio
async def test_free_photo_prefers_pollinations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _fake_cascade(prompt: str, **_k) -> GeminiImageResult:
        calls.append(prompt)
        return GeminiImageResult(data=b"flux-free")

    monkeypatch.setattr(generation_jobs, "generate_free_tier_image", _fake_cascade)

    result = await generation_jobs._generate_free_tier_photo(
        "a cat",
        bot=object(),  # type: ignore[arg-type]
        file_id=None,
    )
    assert result.data == b"flux-free"
    assert calls == ["a cat"]


@pytest.mark.asyncio
async def test_paid_tier_flux_still_uses_replicate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_pollinations(_prompt: str) -> GeminiImageResult:
        raise AssertionError("Pollinations must not be called for paid flux")

    async def _fake_replicate(_model: str, _inputs: dict, **_k) -> str:
        return "https://replicate.delivery/photo.webp"

    async def _fake_enhance(_settings, _prompt: str) -> str:
        return "enhanced prompt"

    monkeypatch.setattr(generation_jobs, "generate_flux_schnell_image", _fake_pollinations)
    monkeypatch.setattr(generation_jobs, "call_replicate_model", _fake_replicate)
    monkeypatch.setattr(generation_jobs, "enhance_video_prompt_for_replicate", _fake_enhance)
    monkeypatch.setattr(generation_jobs, "replicate_configured", lambda: True)

    async def _fake_row(_uid: int):
        class _R:
            tariff = "smart"

        return _R()

    monkeypatch.setattr(generation_jobs, "get_user_row", _fake_row)

    result = await generation_jobs._generate_photo_result(
        "flux_schnell",
        "sunset",
        user_id=7,
    )
    assert result == "https://replicate.delivery/photo.webp"
