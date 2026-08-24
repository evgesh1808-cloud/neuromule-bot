"""Flux paid: только OpenRouter fallback-цепочка (без сторонних API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_FLUX_STACK_FALLBACKS,
    resolve_identity_i2i_fallback_models,
)


@pytest.mark.asyncio
async def test_flux_paid_uses_identity_fallbacks_with_reference() -> None:
    from services import generation_jobs

    identity_fallbacks = resolve_identity_i2i_fallback_models(OPENROUTER_FLUX_PAID_MODEL)
    with patch.object(
        generation_jobs,
        "_generate_openrouter_photo_model",
        new_callable=AsyncMock,
        return_value=GeminiImageResult(url="https://cdn.example/flux.webp"),
    ) as mock_or:
        result = await generation_jobs._generate_flux_schnell_paid(
            "landscape",
            reference_data_url="https://api.telegram.org/file/bot/x.jpg",
        )

    assert result.url == "https://cdn.example/flux.webp"
    mock_or.assert_awaited_once_with(
        OPENROUTER_FLUX_PAID_MODEL,
        "landscape",
        aspect_ratio="1:1",
        reference_data_url="https://api.telegram.org/file/bot/x.jpg",
        fallback_models=identity_fallbacks,
        i2i_reference_mode="selfie",
    )


@pytest.mark.asyncio
async def test_flux_paid_t2i_keeps_flux_stack_fallbacks() -> None:
    from services import generation_jobs

    with patch.object(
        generation_jobs,
        "_generate_openrouter_photo_model",
        new_callable=AsyncMock,
        return_value=GeminiImageResult(url="https://cdn.example/flux.webp"),
    ) as mock_or:
        result = await generation_jobs._generate_flux_schnell_paid("landscape")

    assert result.url == "https://cdn.example/flux.webp"
    mock_or.assert_awaited_once_with(
        OPENROUTER_FLUX_PAID_MODEL,
        "landscape",
        aspect_ratio="1:1",
        reference_data_url=None,
        fallback_models=OPENROUTER_FLUX_STACK_FALLBACKS,
        i2i_reference_mode="selfie",
    )


@pytest.mark.asyncio
async def test_flux_paid_requires_openrouter_key() -> None:
    from services import generation_jobs

    with patch.object(generation_jobs, "openrouter_images_configured", return_value=False):
        with pytest.raises(ExternalApiError, match="OPENROUTER"):
            await generation_jobs._generate_flux_schnell_paid("landscape")


@pytest.mark.asyncio
async def test_free_tier_edit_mode_uses_edit_prompt_not_selfie(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import generation_jobs

    async def _fake_load(**_kwargs: object) -> tuple[bytes, str]:
        return b"ref-bytes", "image/jpeg"

    async def _fake_resolve_url(*_args: object, **_kwargs: object) -> str:
        return "data:image/png;base64,abc"

    async def _fake_edit_prompt(user_prompt: str, *, reference_data_url: str | None) -> str:
        assert reference_data_url
        return f"EDIT:{user_prompt}"

    async def _fake_generate(
        prompt: str,
        *,
        reference_image_bytes: bytes | None,
        reference_mime: str,
    ) -> GeminiImageResult:
        assert prompt.startswith("EDIT:")
        assert reference_image_bytes == b"ref-bytes"
        assert reference_mime == "image/jpeg"
        return GeminiImageResult(url="https://cdn.example/edited.webp")

    monkeypatch.setattr(generation_jobs, "_load_reference_image_bytes", _fake_load)
    monkeypatch.setattr(generation_jobs, "_resolve_reference_data_url", _fake_resolve_url)
    monkeypatch.setattr(generation_jobs, "_resolve_free_tier_edit_prompt", _fake_edit_prompt)
    monkeypatch.setattr(generation_jobs, "generate_free_tier_image", _fake_generate)

    result = await generation_jobs._generate_free_tier_photo(
        "добавь солнце",
        bot=None,
        file_id="AgAC_result",
        i2i_reference_mode="edit",
    )
    assert result.url == "https://cdn.example/edited.webp"
