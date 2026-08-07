"""OpenRouter i2i: reference_image_url (VK CDN) параллельно Telegram file_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import OPENROUTER_NANO_BANANA2_MODEL


@pytest.mark.asyncio
async def test_nano_banana2_i2i_from_reference_image_url() -> None:
    from services import generation_jobs

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
            return_value=GeminiImageResult(url="https://cdn.example/vk-i2i.png"),
        ) as mock_or,
    ):
        result = await generation_jobs._generate_photo_result(
            "nano_banana2",
            "make it cinematic",
            reference_image_url="https://sun9.userapi.com/photo.jpg",
        )

    assert result.url == "https://cdn.example/vk-i2i.png"
    mock_refs.assert_awaited_once_with(
        None,
        None,
        "https://sun9.userapi.com/photo.jpg",
        None,
        "image/jpeg",
    )
    mock_or.assert_awaited_once_with(
        OPENROUTER_NANO_BANANA2_MODEL,
        "make it cinematic",
        aspect_ratio="1:1",
        input_references=[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}}],
        fallback_models=("google/gemini-3.1-flash-image",),
    )


@pytest.mark.asyncio
async def test_openrouter_input_refs_from_url() -> None:
    from services import generation_jobs

    with patch.object(
        generation_jobs,
        "_reference_image_data_url",
        new_callable=AsyncMock,
        return_value="data:image/jpeg;base64,QQ==",
    ) as mock_data:
        refs = await generation_jobs._openrouter_input_refs(
            None,
            None,
            "https://cdn.example/ref.jpg",
        )

    assert refs is not None
    assert refs[0]["image_url"]["url"] == "data:image/jpeg;base64,QQ=="
    mock_data.assert_awaited_once_with(
        bot=None,
        file_id=None,
        reference_image_url="https://cdn.example/ref.jpg",
        reference_image_bytes=None,
        reference_mime="image/jpeg",
    )
