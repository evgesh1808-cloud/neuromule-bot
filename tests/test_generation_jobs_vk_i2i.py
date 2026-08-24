"""OpenRouter identity refs: reference_image_url (VK CDN) параллельно Telegram file_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import NANO_BANANO2_FALLBACKS, OPENROUTER_NANO_BANANA2_MODEL


@pytest.mark.asyncio
async def test_nano_banana2_i2i_from_reference_image_url() -> None:
    from services import generation_jobs

    png_ref = "data:image/png;base64,abc"
    with (
        patch.object(
            generation_jobs,
            "_resolve_reference_data_url",
            new_callable=AsyncMock,
            return_value=png_ref,
        ) as mock_ref,
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
    mock_ref.assert_awaited_once()
    mock_or.assert_awaited_once_with(
        OPENROUTER_NANO_BANANA2_MODEL,
        "make it cinematic",
        aspect_ratio="1:1",
        reference_data_url=png_ref,
        fallback_models=NANO_BANANO2_FALLBACKS,
        i2i_reference_mode="selfie",
    )


@pytest.mark.asyncio
async def test_resolve_reference_data_url_from_http() -> None:
    from services import generation_jobs

    with patch.object(
        generation_jobs,
        "_load_reference_image_bytes",
        new_callable=AsyncMock,
        return_value=(b"\xff\xd8\xff\xd9", "image/jpeg"),
    ), patch(
        "services.openrouter_images.reference_bytes_to_png_data_url",
        return_value="data:image/png;base64,abc",
    ):
        url = await generation_jobs._resolve_reference_data_url(
            None,
            None,
            "https://cdn.example/ref.jpg",
        )

    assert url == "data:image/png;base64,abc"


@pytest.mark.asyncio
async def test_resolve_reference_data_url_from_telegram_file_id() -> None:
    from services import generation_jobs

    bot = AsyncMock()
    with patch.object(
        generation_jobs,
        "_load_reference_image_bytes",
        new_callable=AsyncMock,
        return_value=(b"\xff\xd8\xff\xd9", "image/jpeg"),
    ), patch(
        "services.openrouter_images.reference_bytes_to_png_data_url",
        return_value="data:image/png;base64,tg",
    ):
        url = await generation_jobs._resolve_reference_data_url(
            bot,
            "AgAC_ref",
            None,
        )

    assert url == "data:image/png;base64,tg"
