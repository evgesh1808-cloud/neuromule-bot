"""Тесты OpenRouter Images API клиента."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Settings
from services.api_resilience import ExternalApiError
from services.gemini_image_client import GeminiImageResult
from services.openrouter_images import (
    IDENTITY_NEGATIVE_PROMPT,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_IMAGES_URL,
    REFERENCE_QUALITY_SUFFIX,
    append_face_description_to_prompt,
    append_negative_prompt_directive,
    append_reference_quality_modifiers,
    format_identity_photo_prompt,
    generate_openrouter_image,
    generate_openrouter_photo,
    openrouter_identity_reference,
    openrouter_input_reference,
    parse_openrouter_image_payload,
    reference_url_to_data_url,
    resolve_openrouter_photo_prompt_and_refs,
    resolve_openrouter_reference_url,
)


def test_parse_openrouter_image_payload_url() -> None:
    result = parse_openrouter_image_payload(
        {"data": [{"url": "https://cdn.example.com/out.webp"}]}
    )
    assert result.url == "https://cdn.example.com/out.webp"


def test_parse_openrouter_image_payload_b64() -> None:
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
    assert "negative_prompt" not in body


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


def test_openrouter_input_reference_strict_schema() -> None:
    ref = openrouter_input_reference("https://api.telegram.org/file/bot123/photos/x.jpg")
    assert ref == {
        "type": "image_url",
        "image_url": {"url": "https://api.telegram.org/file/bot123/photos/x.jpg"},
    }


def test_openrouter_identity_reference_is_image_url_alias() -> None:
    url = "https://api.telegram.org/file/bot123/photos/x.jpg"
    assert openrouter_identity_reference(url) == openrouter_input_reference(url)


def test_append_reference_quality_modifiers() -> None:
    out = append_reference_quality_modifiers("sunset portrait")
    assert out.endswith("highly detailed face")
    assert REFERENCE_QUALITY_SUFFIX in out
    assert append_reference_quality_modifiers(out) == out


def test_append_negative_prompt_directive() -> None:
    out = append_negative_prompt_directive("sunset portrait")
    assert f"[Negative prompt: {IDENTITY_NEGATIVE_PROMPT}]" in out
    assert append_negative_prompt_directive(out) == out


@pytest.mark.asyncio
async def test_resolve_openrouter_reference_url_from_telegram_file_id() -> None:
    bot = MagicMock()
    with patch(
        "services.replicate_client.telegram_photo_download_url",
        AsyncMock(return_value="https://api.telegram.org/file/botT/photos/ref.jpg"),
    ) as dl:
        url = await resolve_openrouter_reference_url(bot=bot, file_id="AgACabc")
    assert url == "https://api.telegram.org/file/botT/photos/ref.jpg"
    dl.assert_awaited_once_with(bot, "AgACabc")


@pytest.mark.asyncio
async def test_reference_url_to_data_url_from_https() -> None:
    photo_bytes = b"\xff\xd8\xff\xd9"
    with patch(
        "services.streaming_download.stream_download_to_bytes",
        AsyncMock(return_value=photo_bytes),
    ):
        data_url = await reference_url_to_data_url("https://cdn.example.com/face.jpg")
    assert data_url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == photo_bytes


def test_format_identity_photo_prompt_wraps_user_text() -> None:
    out = format_identity_photo_prompt("sunset portrait")
    assert out.startswith("sunset portrait.")
    assert "facial identity" in out


@pytest.mark.asyncio
async def test_generate_openrouter_image_with_image_url_reference() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
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
            input_references=[openrouter_input_reference("https://cdn.example.com/ref.jpg")],
        )

    assert result.url == "https://cdn.example.com/identity.webp"
    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"] == [
        {"type": "image_url", "image_url": {"url": "https://cdn.example.com/ref.jpg"}},
    ]
    assert "negative_prompt" not in body


@pytest.mark.asyncio
async def test_generate_openrouter_image_logs_full_error_response() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    zod_error = 'ZodError: invalid_value at input_references[0].type'
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = zod_error
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "services.openrouter_http.get_openrouter_http_client",
            AsyncMock(return_value=mock_client),
        ),
        patch("services.openrouter_images.logger") as mock_logger,
    ):
        with pytest.raises(ExternalApiError) as exc:
            await generate_openrouter_image(
                settings,
                model=OPENROUTER_FLUX_PAID_MODEL,
                prompt="test",
                input_references=[openrouter_input_reference("https://cdn.example.com/ref.jpg")],
            )

    assert zod_error in str(exc.value)
    mock_logger.error.assert_called_once()
    assert zod_error in mock_logger.error.call_args.args[-1]


@pytest.mark.asyncio
async def test_resolve_flux_image_url_refs_and_negative_in_prompt() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    tg_ref = "https://api.telegram.org/file/bot123/photos/user.jpg"
    prompt, refs = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_FLUX_PAID_MODEL,
        user_prompt="sunset portrait",
        reference_data_url=tg_ref,
    )
    assert REFERENCE_QUALITY_SUFFIX in prompt
    assert f"[Negative prompt: {IDENTITY_NEGATIVE_PROMPT}]" in prompt
    assert refs == [
        {"type": "image_url", "image_url": {"url": tg_ref}},
    ]


@pytest.mark.asyncio
async def test_generate_openrouter_photo_negative_in_prompt_not_body() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/flux.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        await generate_openrouter_photo(
            settings,
            model=OPENROUTER_FLUX_PAID_MODEL,
            user_prompt="sunset portrait",
            reference_data_url="https://api.telegram.org/file/bot123/photos/user.jpg",
        )

    body = mock_client.post.await_args.kwargs["json"]
    assert "negative_prompt" not in body
    assert f"[Negative prompt: {IDENTITY_NEGATIVE_PROMPT}]" in body["prompt"]
    assert body["input_references"][0]["type"] == "image_url"


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
            reference_data_url="https://api.telegram.org/file/bot123/photos/face.jpg",
        )
    assert refs is None
    assert "Subject face:" in prompt
    assert "brown eyes" in prompt
    assert REFERENCE_QUALITY_SUFFIX in prompt
    assert f"[Negative prompt: {IDENTITY_NEGATIVE_PROMPT}]" in prompt


@pytest.mark.asyncio
async def test_describe_reference_face_uses_data_url() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    photo_bytes = b"\xff\xd8\xff\xd9"
    data_url = f"data:image/jpeg;base64,{base64.b64encode(photo_bytes).decode('ascii')}"

    with (
        patch(
            "services.openrouter_images.reference_url_to_data_url",
            AsyncMock(return_value=data_url),
        ),
        patch(
            "services.ai_text.ask_ai_messages",
            AsyncMock(return_value=MagicMock(content="blue eyes, fair skin, short hair")),
        ) as ask,
    ):
        from services.openrouter_images import describe_reference_face_for_prompt

        desc = await describe_reference_face_for_prompt(
            settings,
            "https://api.telegram.org/file/bot123/photos/face.jpg",
        )

    assert desc == "blue eyes, fair skin, short hair"
    messages = ask.await_args.args[1]
    image_part = messages[1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_generate_openrouter_photo_gpt_text_only_body() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
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
            reference_data_url="https://api.telegram.org/file/bot123/photos/face.jpg",
        )

    assert result.url == "https://cdn.example.com/gpt.webp"
    body = mock_client.post.await_args.kwargs["json"]
    assert body["model"] == OPENROUTER_GPT_IMAGE2_MODEL
    assert "Subject face:" in body["prompt"]
    assert "input_references" not in body
    assert "negative_prompt" not in body


def test_append_face_description_to_prompt() -> None:
    out = append_face_description_to_prompt("cyberpunk", "blue eyes, freckles")
    assert out == "cyberpunk. Subject face: blue eyes, freckles"
