"""Тесты OpenRouter Images API клиента."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Settings
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    OPENROUTER_FLUX_SCHNELL_MODEL,
    OPENROUTER_IMAGES_URL,
    generate_openrouter_image,
    parse_openrouter_image_payload,
)


def test_parse_openrouter_image_payload_url() -> None:
    result = parse_openrouter_image_payload(
        {"data": [{"url": "https://cdn.example.com/out.webp"}]}
    )
    assert result.url == "https://cdn.example.com/out.webp"


def test_parse_openrouter_image_payload_b64() -> None:
    import base64

    raw = base64.b64encode(b"\xff\xd8\xffjpeg").decode("ascii")
    result = parse_openrouter_image_payload({"data": [{"b64_json": raw}]})
    assert result.data == b"\xff\xd8\xffjpeg"


@pytest.mark.asyncio
async def test_generate_openrouter_image_posts_images_endpoint() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/flux.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        result = await generate_openrouter_image(
            settings,
            model=OPENROUTER_FLUX_SCHNELL_MODEL,
            prompt="sunset over mountains",
            aspect_ratio="1:1",
        )

    assert result.url == "https://cdn.example.com/flux.webp"
    call = mock_client.post.await_args
    assert call.args[0] == OPENROUTER_IMAGES_URL
    body = call.kwargs["json"]
    assert body["model"] == OPENROUTER_FLUX_SCHNELL_MODEL
    assert body["aspect_ratio"] == "1:1"
    assert body["prompt"] == "sunset over mountains"


@pytest.mark.asyncio
async def test_generate_openrouter_image_missing_key() -> None:
    settings = Settings(tg_token="t", openrouter_key="")
    with pytest.raises(ExternalApiError) as exc:
        await generate_openrouter_image(
            settings,
            model=OPENROUTER_FLUX_SCHNELL_MODEL,
            prompt="test",
        )
    assert exc.value.provider == "OpenRouter"
