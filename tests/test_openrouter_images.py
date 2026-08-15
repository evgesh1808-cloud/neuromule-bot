"""Тесты OpenRouter Images API клиента (умный роутинг + base64 refs)."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Settings
from services.openrouter_images import (
    FLUX_OPENAI_FILM_SUFFIX,
    FLUX_OPENAI_NEGATIVE_IN_PROMPT,
    NANO_BANANO_NEGATIVE_PROMPT,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_IMAGES_URL,
    OPENROUTER_NANO_BANANA2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
    SELFIE_WOMAN_PROMPT_PREFIX,
    append_reference_prompt_modifiers,
    generate_openrouter_image,
    generate_openrouter_photo,
    is_google_image_face_stack,
    openrouter_face_reference,
    openrouter_input_reference,
    prepend_selfie_woman_prompt,
    reference_url_to_data_url,
    resolve_openrouter_photo_prompt_and_refs,
)

_DATA_URL = "data:image/jpeg;base64,/9j/face"
_TG_URL = "https://api.telegram.org/file/bot123/photos/user.jpg"


@pytest.fixture(autouse=True)
def _mock_ref_to_data_url() -> AsyncMock:
    with patch(
        "services.openrouter_images.reference_url_to_data_url",
        AsyncMock(return_value=_DATA_URL),
    ) as mock:
        yield mock


def test_prepend_selfie_woman_prompt() -> None:
    out = prepend_selfie_woman_prompt("sunset in paris")
    assert out.startswith(SELFIE_WOMAN_PROMPT_PREFIX)
    assert "sunset in paris" in out


def test_is_google_image_face_stack_includes_fallbacks() -> None:
    assert is_google_image_face_stack(OPENROUTER_NANO_BANANA_PRO_MODEL)
    assert is_google_image_face_stack("google/gemini-3.1-flash-image")
    assert is_google_image_face_stack("google/gemini-3-pro-image-preview")
    assert not is_google_image_face_stack(OPENROUTER_FLUX_PAID_MODEL)


def test_openrouter_face_reference_schema() -> None:
    ref = openrouter_face_reference(_DATA_URL)
    assert ref == {"type": "image_url", "image_url": {"url": _DATA_URL}}


@pytest.mark.asyncio
async def test_resolve_flux_uses_base64_image_url_ref() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_FLUX_PAID_MODEL,
        user_prompt="sunset portrait",
        reference_data_url=_TG_URL,
    )
    assert prompt.startswith(SELFIE_WOMAN_PROMPT_PREFIX)
    assert FLUX_OPENAI_FILM_SUFFIX in prompt
    assert refs == [{"type": "image_url", "image_url": {"url": _DATA_URL}}]
    assert extras == {}


@pytest.mark.asyncio
async def test_resolve_nano_face_ref_and_body_extensions() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_NANO_BANANA2_MODEL,
        user_prompt="sunset portrait",
        reference_data_url=_TG_URL,
    )
    assert prompt.startswith(SELFIE_WOMAN_PROMPT_PREFIX)
    assert "face reference" in prompt
    assert "[Negative prompt:" in prompt
    assert refs == [{"type": "image_url", "image_url": {"url": _DATA_URL}}]
    assert extras == {}


@pytest.mark.asyncio
async def test_resolve_gemini_fallback_keeps_face_ref() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model="google/gemini-3-pro-image-preview",
        user_prompt="portrait",
        reference_input_url=_DATA_URL,
    )
    assert refs == [{"type": "image_url", "image_url": {"url": _DATA_URL}}]
    assert extras == {}
    assert "[Negative prompt:" in prompt


@pytest.mark.asyncio
async def test_generate_openrouter_photo_flux_base64_ref_in_body() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.json.return_value = {"data": [{"url": "https://cdn.example/flux.webp"}]}
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
            reference_data_url=_TG_URL,
        )

    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "negative_prompt" not in body
    assert "identity" not in body


@pytest.mark.asyncio
async def test_generate_openrouter_photo_fallback_preserves_base64_ref() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    ok = MagicMock()
    ok.status_code = 200
    ok.text = ""
    ok.json.return_value = {"data": [{"url": "https://cdn.example/fallback.webp"}]}
    fail = MagicMock()
    fail.status_code = 400
    fail.text = "ZodError: invalid"
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[fail, ok])

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        result = await generate_openrouter_photo(
            settings,
            model=OPENROUTER_NANO_BANANA_PRO_MODEL,
            user_prompt="portrait",
            reference_data_url=_TG_URL,
            fallback_models=("google/gemini-3-pro-image-preview",),
        )

    assert result.url == "https://cdn.example/fallback.webp"
    second_body = mock_client.post.await_args_list[1].kwargs["json"]
    assert second_body["input_references"][0]["type"] == "image_url"
    assert second_body["input_references"][0]["image_url"]["url"].startswith("data:")
    assert "identity" not in second_body
    assert "negative_prompt" not in second_body


@pytest.mark.asyncio
async def test_resolve_gpt_image2_face_description_not_refs() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    with patch(
        "services.openrouter_images.describe_reference_face_for_prompt",
        AsyncMock(return_value="A photo of a young woman, oval face, brown eyes"),
    ):
        prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
            settings,
            model=OPENROUTER_GPT_IMAGE2_MODEL,
            user_prompt="studio headshot",
            reference_data_url=_TG_URL,
        )
    assert refs is None
    assert extras == {}
    assert "Subject face:" in prompt
    assert "young woman" in prompt


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
async def test_generate_openrouter_image_no_zod_fields_for_flux() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = ""
    mock_response.json.return_value = {"data": [{"url": "https://cdn.example/out.webp"}]}
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        await generate_openrouter_image(
            settings,
            model=OPENROUTER_FLUX_PAID_MODEL,
            prompt="test",
            input_references=[openrouter_input_reference(_DATA_URL)],
        )

    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"][0]["type"] == "image_url"
    assert "identity" not in body
    assert "negative_prompt" not in body
