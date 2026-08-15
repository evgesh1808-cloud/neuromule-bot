"""Тесты OpenRouter Images API клиента (умный роутинг стеков)."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Settings
from services.api_resilience import ExternalApiError
from services.openrouter_images import (
    FLUX_OPENAI_FILM_SUFFIX,
    FLUX_OPENAI_NEGATIVE_IN_PROMPT,
    NANO_BANANO_NEGATIVE_PROMPT,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_IMAGES_URL,
    OPENROUTER_NANO_BANANA2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
    append_face_description_to_prompt,
    append_negative_prompt_directive,
    append_reference_prompt_modifiers,
    generate_openrouter_image,
    generate_openrouter_photo,
    is_nano_banano_stack,
    is_openai_flux_stack,
    openrouter_character_reference,
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


def test_stack_detection() -> None:
    assert is_nano_banano_stack(OPENROUTER_NANO_BANANA_PRO_MODEL)
    assert is_nano_banano_stack("google/nano-banano-preview")
    assert is_openai_flux_stack(OPENROUTER_FLUX_PAID_MODEL)
    assert is_openai_flux_stack(OPENROUTER_GPT_IMAGE2_MODEL)
    assert not is_nano_banano_stack(OPENROUTER_FLUX_PAID_MODEL)


def test_append_flux_prompt_modifiers_anti_gloss() -> None:
    out = append_reference_prompt_modifiers("sunset portrait", OPENROUTER_FLUX_PAID_MODEL)
    assert "35mm film" in out
    assert "natural skin texture" in out
    assert "look sharp and gorgeous" not in out
    assert "highly detailed face" not in out
    assert f"[Negative prompt: {FLUX_OPENAI_NEGATIVE_IN_PROMPT}]" in out


def test_append_nano_prompt_modifiers_character_hint() -> None:
    out = append_reference_prompt_modifiers("cyberpunk portrait", OPENROUTER_NANO_BANANA_PRO_MODEL)
    assert "character reference" in out
    assert FLUX_OPENAI_FILM_SUFFIX not in out


def test_openrouter_input_reference_strict_schema() -> None:
    ref = openrouter_input_reference("https://api.telegram.org/file/bot123/photos/x.jpg")
    assert ref == {
        "type": "image_url",
        "image_url": {"url": "https://api.telegram.org/file/bot123/photos/x.jpg"},
    }


def test_openrouter_character_reference_schema() -> None:
    ref = openrouter_character_reference("https://cdn.example.com/selfie.jpg")
    assert ref == {
        "type": "character",
        "image_url": {"url": "https://cdn.example.com/selfie.jpg"},
    }


@pytest.mark.asyncio
async def test_generate_openrouter_image_posts_images_endpoint() -> None:
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
        result = await generate_openrouter_image(
            settings,
            model=OPENROUTER_FLUX_PAID_MODEL,
            prompt="sunset over mountains",
            aspect_ratio="1:1",
        )

    assert result.url == "https://cdn.example.com/flux.webp"
    body = mock_client.post.await_args.kwargs["json"]
    assert body["model"] == OPENROUTER_FLUX_PAID_MODEL
    assert "negative_prompt" not in body
    assert "identity" not in body


@pytest.mark.asyncio
async def test_generate_openrouter_image_nano_body_extensions() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/nano.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        await generate_openrouter_image(
            settings,
            model=OPENROUTER_NANO_BANANA_PRO_MODEL,
            prompt="portrait in paris",
            input_references=[openrouter_character_reference("https://cdn.example.com/face.jpg")],
            body_extensions={
                "identity": True,
                "identity_weight": 1.0,
                "negative_prompt": NANO_BANANO_NEGATIVE_PROMPT,
            },
        )

    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"][0]["type"] == "character"
    assert body["identity"] is True
    assert body["identity_weight"] == 1.0
    assert body["negative_prompt"] == NANO_BANANO_NEGATIVE_PROMPT


@pytest.mark.asyncio
async def test_resolve_flux_image_url_refs_negative_in_prompt_only() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    tg_ref = "https://api.telegram.org/file/bot123/photos/user.jpg"
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_FLUX_PAID_MODEL,
        user_prompt="sunset portrait",
        reference_data_url=tg_ref,
    )
    assert FLUX_OPENAI_FILM_SUFFIX in prompt
    assert f"[Negative prompt: {FLUX_OPENAI_NEGATIVE_IN_PROMPT}]" in prompt
    assert refs == [{"type": "image_url", "image_url": {"url": tg_ref}}]
    assert extras == {}


@pytest.mark.asyncio
async def test_resolve_nano_character_refs_and_body_extensions() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    tg_ref = "https://api.telegram.org/file/bot123/photos/user.jpg"
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_NANO_BANANA2_MODEL,
        user_prompt="sunset portrait",
        reference_data_url=tg_ref,
    )
    assert "character reference" in prompt
    assert refs == [{"type": "character", "image_url": {"url": tg_ref}}]
    assert extras["identity"] is True
    assert extras["identity_weight"] == 1.0
    assert extras["negative_prompt"] == NANO_BANANO_NEGATIVE_PROMPT


@pytest.mark.asyncio
async def test_generate_openrouter_photo_flux_no_root_negative() -> None:
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
    assert body["input_references"][0]["type"] == "image_url"
    assert "35mm film" in body["prompt"]


@pytest.mark.asyncio
async def test_generate_openrouter_photo_nano_character_in_body() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/nano.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        await generate_openrouter_photo(
            settings,
            model=OPENROUTER_NANO_BANANA_PRO_MODEL,
            user_prompt="sunset portrait",
            reference_data_url="https://api.telegram.org/file/bot123/photos/user.jpg",
        )

    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"][0]["type"] == "character"
    assert body["negative_prompt"] == NANO_BANANO_NEGATIVE_PROMPT
    assert body["identity"] is True


@pytest.mark.asyncio
async def test_resolve_gpt_image2_face_description_not_refs() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    with patch(
        "services.openrouter_images.describe_reference_face_for_prompt",
        AsyncMock(return_value="oval face, brown eyes, short dark hair"),
    ):
        prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
            settings,
            model=OPENROUTER_GPT_IMAGE2_MODEL,
            user_prompt="studio headshot",
            reference_data_url="https://api.telegram.org/file/bot123/photos/face.jpg",
        )
    assert refs is None
    assert extras == {}
    assert "Subject face:" in prompt
    assert "35mm film" in prompt
    assert "negative_prompt" not in extras


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
        from services.openrouter_images import (
            FACE_DESCRIBE_SYSTEM_PROMPT,
            describe_reference_face_for_prompt,
        )

        desc = await describe_reference_face_for_prompt(
            settings,
            "https://api.telegram.org/file/bot123/photos/face.jpg",
        )

    assert desc == "blue eyes, fair skin, short hair"
    messages = ask.await_args.args[1]
    assert messages[0]["content"] == FACE_DESCRIBE_SYSTEM_PROMPT
    assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_reference_url_to_data_url_from_https() -> None:
    photo_bytes = b"\xff\xd8\xff\xd9"
    with patch(
        "services.streaming_download.stream_download_to_bytes",
        AsyncMock(return_value=photo_bytes),
    ):
        data_url = await reference_url_to_data_url("https://cdn.example.com/face.jpg")
    assert data_url.startswith("data:image/jpeg;base64,")


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


def test_append_face_description_to_prompt() -> None:
    out = append_face_description_to_prompt("cyberpunk", "blue eyes, freckles")
    assert out == "cyberpunk. Subject face: blue eyes, freckles"


def test_append_negative_prompt_directive_idempotent() -> None:
    out = append_negative_prompt_directive("sunset")
    assert append_negative_prompt_directive(out) == out
