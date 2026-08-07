"""Платные Imagen / Nano Banana → OpenRouter Images (не прямой Gemini API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_IMAGEN4_FAST_MODEL,
    OPENROUTER_NANO_BANANA2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_key", "or_model"),
    [
        ("imagen4", OPENROUTER_IMAGEN4_FAST_MODEL),
        ("dalle_3", OPENROUTER_GPT_IMAGE2_MODEL),
        ("nano_banana2", OPENROUTER_NANO_BANANA2_MODEL),
        ("nano_banana_pro", OPENROUTER_NANO_BANANA_PRO_MODEL),
    ],
)
async def test_paid_google_models_use_openrouter_images(
    model_key: str,
    or_model: str,
) -> None:
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
    assert call.kwargs["input_references"] is None
    assert call.kwargs.get("aspect_ratio") == "1:1"
    if model_key == "nano_banana2":
        assert call.kwargs["fallback_models"] == ("google/gemini-3.1-flash-image",)
    elif model_key == "nano_banana_pro":
        assert call.kwargs["fallback_models"] == ("google/gemini-3-pro-image-preview",)
    else:
        assert call.kwargs.get("fallback_models", ()) == ()


@pytest.mark.asyncio
async def test_nano_banana2_i2i_passes_input_references() -> None:
    from services import generation_jobs

    bot = MagicMock()
    with (
        patch.object(
            generation_jobs,
            "_openrouter_input_refs",
            new_callable=AsyncMock,
            return_value=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}}],
        ) as mock_refs,
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
    mock_refs.assert_awaited_once_with(bot, "AgACAgIAAxkB", None, None, "image/jpeg")
    mock_or.assert_awaited_once_with(
        OPENROUTER_NANO_BANANA2_MODEL,
        "make it cinematic",
        aspect_ratio="1:1",
        input_references=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}}],
        fallback_models=("google/gemini-3.1-flash-image",),
    )
