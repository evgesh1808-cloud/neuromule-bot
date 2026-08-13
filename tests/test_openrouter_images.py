"""Тесты OpenRouter Images API клиента."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Settings
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    IDENTITY_REFERENCE_WEIGHT,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_IMAGES_URL,
    append_face_description_to_prompt,
    format_identity_photo_prompt,
    generate_openrouter_image,
    generate_openrouter_photo,
    openrouter_identity_reference,
    parse_openrouter_image_payload,
    resolve_openrouter_photo_prompt_and_refs,
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
            model=OPENROUTER_FLUX_PAID_MODEL,
            prompt="sunset over mountains",
            aspect_ratio="1:1",
        )

    assert result.url == "https://cdn.example.com/flux.webp"
    call = mock_client.post.await_args
    assert call.args[0] == OPENROUTER_IMAGES_URL
    body = call.kwargs["json"]
    assert body["model"] == OPENROUTER_FLUX_PAID_MODEL
    assert body["aspect_ratio"] == "1:1"
    assert body["prompt"] == "sunset over mountains"


@pytest.mark.asyncio
async def test_generate_openrouter_image_missing_key() -> None:
    settings = Settings(tg_token="t", openrouter_key="")
    with pytest.raises(ExternalApiError) as exc:
        await generate_openrouter_image(
            settings,
            model=OPENROUTER_FLUX_PAID_MODEL,
            prompt="test",
        )
    assert exc.value.provider == "OpenRouter"


def test_openrouter_identity_reference_type_and_weight() -> None:
    ref = openrouter_identity_reference("data:image/jpeg;base64,abc")
    assert ref["type"] == "identity"
    assert ref["weight"] == IDENTITY_REFERENCE_WEIGHT
    assert ref["image_url"]["url"] == "data:image/jpeg;base64,abc"


def test_format_identity_photo_prompt_wraps_user_text() -> None:
    out = format_identity_photo_prompt("sunset portrait")
    assert out.startswith("sunset portrait.")
    assert "facial identity" in out


@pytest.mark.asyncio
async def test_generate_openrouter_image_with_identity_reference() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/identity.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        result = await generate_openrouter_image(
            settings,
            model=OPENROUTER_FLUX_PAID_MODEL,
            prompt=format_identity_photo_prompt("studio headshot"),
            aspect_ratio="1:1",
            input_references=[openrouter_identity_reference("data:image/jpeg;base64,abc")],
        )

    assert result.url == "https://cdn.example.com/identity.webp"
    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"][0]["type"] == "identity"
    assert body["input_references"][0]["weight"] == IDENTITY_REFERENCE_WEIGHT


@pytest.mark.asyncio
async def test_resolve_flux_identity_refs_with_weight() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_FLUX_PAID_MODEL,
        user_prompt="sunset portrait",
        reference_data_url="data:image/jpeg;base64,abc",
    )
    assert prompt == "sunset portrait"
    assert refs == [
        {
            "type": "identity",
            "weight": IDENTITY_REFERENCE_WEIGHT,
            "image_url": {"url": "data:image/jpeg;base64,abc"},
        }
    ]


@pytest.mark.asyncio
async def test_resolve_gpt_image2_uses_face_description_not_refs() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    with patch(
        "services.openrouter_images.describe_reference_face_for_prompt",
        AsyncMock(return_value="oval face, brown eyes, short dark hair"),
    ):
        prompt, refs = await resolve_openrouter_photo_prompt_and_refs(
            settings,
            model=OPENROUTER_GPT_IMAGE2_MODEL,
            user_prompt="studio headshot",
            reference_data_url="data:image/jpeg;base64,face",
        )
    assert refs is None
    assert "Subject face:" in prompt
    assert "brown eyes" in prompt


@pytest.mark.asyncio
async def test_generate_openrouter_photo_gpt_text_only_body() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/gpt.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "services.openrouter_http.get_openrouter_http_client",
            AsyncMock(return_value=mock_client),
        ),
        patch(
            "services.openrouter_images.describe_reference_face_for_prompt",
            AsyncMock(return_value="sharp jawline, green eyes"),
        ),
    ):
        result = await generate_openrouter_photo(
            settings,
            model=OPENROUTER_GPT_IMAGE2_MODEL,
            user_prompt="cyberpunk portrait",
            reference_data_url="data:image/jpeg;base64,abc",
        )

    assert result.url == "https://cdn.example.com/gpt.webp"
    body = mock_client.post.await_args.kwargs["json"]
    assert body["model"] == OPENROUTER_GPT_IMAGE2_MODEL
    assert "Subject face:" in body["prompt"]
    assert "input_references" not in body


def test_append_face_description_to_prompt() -> None:
    out = append_face_description_to_prompt("cyberpunk", "blue eyes, freckles")
    assert out == "cyberpunk. Subject face: blue eyes, freckles"
