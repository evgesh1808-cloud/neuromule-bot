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
