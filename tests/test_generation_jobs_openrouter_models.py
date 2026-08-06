"""Платные Imagen / Nano Banana → OpenRouter Images (не прямой Gemini API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    OPENROUTER_IMAGEN4_FAST_MODEL,
    OPENROUTER_NANO_BANANA2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_key", "or_model"),
    [
        ("imagen4", OPENROUTER_IMAGEN4_FAST_MODEL),
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
    mock_or.assert_awaited_once_with(
        or_model,
        "a red apple on white table",
        input_references=None,
    )


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
    mock_refs.assert_awaited_once_with(bot, "AgACAgIAAxkB")
    mock_or.assert_awaited_once_with(
        OPENROUTER_NANO_BANANA2_MODEL,
        "make it cinematic",
        input_references=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}}],
    )
