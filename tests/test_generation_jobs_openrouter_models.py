"""Платные Flux / GPT Image 2 / Nano Banana → OpenRouter Images."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    NANO_BANANO_PRO_FALLBACKS,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_key", "or_model"),
    [
        ("flux_schnell", OPENROUTER_FLUX_PAID_MODEL),
        ("dalle_3", OPENROUTER_GPT_IMAGE2_MODEL),
        ("nano_banana2", OPENROUTER_FLUX_PAID_MODEL),
        ("nano_banana_pro", OPENROUTER_FLUX_PAID_MODEL),
    ],
)
async def test_paid_models_use_openrouter_images_with_smart_routing_t2i(
    model_key: str,
    or_model: str,
) -> None:
    """Без референса Nano → Flux (Chatcom-style text routing)."""
    from services import generation_jobs

    with patch.object(
        generation_jobs,
        "_generate_openrouter_photo_model",
        new_callable=AsyncMock,
        return_value=GeminiImageResult(url="https://cdn.example/out.png"),
    ) as mock_or:
        result = await generation_jobs._generate_photo_result(
            model_key,
            "a red apple on white table",
        )

    assert result.url == "https://cdn.example/out.png"
    mock_or.assert_awaited_once()
    call = mock_or.await_args
    assert call.args[0] == or_model
    assert call.args[1] == "a red apple on white table"
    assert call.kwargs.get("reference_data_url") is None
    assert call.kwargs.get("aspect_ratio") == "1:1"


@pytest.mark.asyncio
async def test_selfie_routes_to_nano_banana_pro_openrouter() -> None:
    from services import generation_jobs

    bot = MagicMock()
    with (
        patch.object(
            generation_jobs,
            "_resolve_reference_data_url",
            new_callable=AsyncMock,
            return_value="data:image/jpeg;base64,abc",
        ),
        patch.object(
            generation_jobs,
            "_generate_openrouter_photo_model",
            new_callable=AsyncMock,
            return_value=GeminiImageResult(url="https://cdn.example/i2i.png"),
        ) as mock_or,
    ):
        result = await generation_jobs._generate_photo_result(
            "nano_banana2",
            "make it cinematic",
            bot=bot,
            file_id="AgACAgIAAxkB",
        )

    assert result.url == "https://cdn.example/i2i.png"
    mock_or.assert_awaited_once_with(
        OPENROUTER_NANO_BANANA_PRO_MODEL,
        "make it cinematic",
        aspect_ratio="1:1",
        reference_data_url="data:image/jpeg;base64,abc",
        fallback_models=NANO_BANANO_PRO_FALLBACKS,
    )
