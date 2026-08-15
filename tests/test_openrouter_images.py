"""Тесты OpenRouter Images API клиента (умный роутинг + base64 refs)."""

from __future__ import annotations

import base64
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from config import Settings
from services.openrouter_images import (
    GOOGLE_IDENTITY_LOCK,
    GOOGLE_SELFIE_I2I_PROMPT_TEMPLATE,
    OPENAI_INPAINT_I2I_PROMPT_TEMPLATE,
    OPENROUTER_FLUX_PAID_MODEL,
    OPENROUTER_GPT_IMAGE2_MODEL,
    OPENROUTER_NANO_BANANA2_MODEL,
    OPENROUTER_NANO_BANANA_PRO_MODEL,
    SELFIE_WOMAN_PROMPT_PREFIX,
    build_selfie_i2i_prompt_for_model,
    generate_openrouter_image,
    generate_openrouter_photo,
    is_google_image_face_stack,
    openrouter_face_reference,
    openrouter_input_reference,
    prepend_selfie_woman_prompt,
    reference_url_to_data_url,
    reference_url_to_png_data_url,
    resolve_openrouter_photo_prompt_and_refs,
)

_DATA_URL = "data:image/jpeg;base64,/9j/face"
_PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="
_TG_URL = "https://api.telegram.org/file/bot123/photos/user.jpg"


@pytest.fixture(autouse=True)
def _mock_ref_to_png() -> AsyncMock:
    with patch(
        "services.openrouter_images.reference_url_to_png_data_url",
        AsyncMock(return_value=_PNG_DATA_URL),
    ) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _mock_translate() -> AsyncMock:
    with patch(
        "services.openrouter_images.translate_photo_user_intent",
        AsyncMock(return_value="sunset in paris"),
    ):
        yield


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
    ref = openrouter_face_reference(_PNG_DATA_URL)
    assert ref == {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}}


def test_build_google_selfie_prompt_template() -> None:
    prompt = build_selfie_i2i_prompt_for_model(
        OPENROUTER_NANO_BANANA2_MODEL,
        "sunset in paris",
    )
    assert "STRICTLY as character identity reference" in prompt
    assert "CRITICAL: Completely override the camera distance" in prompt
    assert "OUTPUT FRAMING (mandatory)" in prompt
    assert "pull the camera back wider" in prompt.lower()
    assert "three-quarter body" in prompt.lower()
    assert "HAIR FRAMING (mandatory)" in prompt
    assert "generous headroom" in prompt.lower()
    assert "shoulders up only" not in prompt.lower()
    assert "identical eye shape" in prompt
    assert "sunset in paris" in prompt
    assert "[Negative prompt:" in prompt
    assert "reference waist-up crop" in prompt
    assert "copied reference framing" in prompt
    assert "cropped hair" in prompt


def test_build_selfie_prompt_tight_framing_only_when_user_asks_headshot() -> None:
    prompt = build_selfie_i2i_prompt_for_model(
        OPENROUTER_NANO_BANANA2_MODEL,
        "studio headshot with soft light",
    )
    assert "close-up headshot or bust portrait" in prompt.lower()
    assert "pull the camera back wider" not in prompt.lower()
    assert "HAIR FRAMING (mandatory)" in prompt
    assert "cropped hair" in prompt


def test_build_selfie_prompt_allows_hair_crop_only_when_user_asks() -> None:
    prompt = build_selfie_i2i_prompt_for_model(
        OPENROUTER_NANO_BANANA2_MODEL,
        "extreme face close-up, crop hair at top of frame",
    )
    assert "HAIR FRAMING: follow the scene description" in prompt
    assert "HAIR FRAMING (mandatory)" not in prompt
    assert "cropped hair" not in prompt


def test_build_flux_selfie_prompt_protects_hair_framing() -> None:
    prompt = build_selfie_i2i_prompt_for_model(
        OPENROUTER_FLUX_PAID_MODEL,
        "sunset in paris",
    )
    assert "generous headroom" in prompt.lower()
    assert "cut-off hair" in prompt


def test_build_selfie_prompt_wider_framing_when_user_asks_full_body() -> None:
    prompt = build_selfie_i2i_prompt_for_model(
        OPENROUTER_NANO_BANANA2_MODEL,
        "full body portrait on the beach at sunset",
    )
    assert "OUTPUT FRAMING: follow the scene description" in prompt
    assert "shoulders up only" not in prompt.lower()


def test_build_openai_inpaint_prompt_template() -> None:
    prompt = build_selfie_i2i_prompt_for_model(
        OPENROUTER_GPT_IMAGE2_MODEL,
        "studio headshot",
    )
    assert prompt.startswith("Inpaint and seamlessly integrate")
    assert "studio headshot" in prompt
    assert "STRICTLY as character identity reference" in prompt
    assert "[Negative prompt:" in prompt


@pytest.mark.asyncio
async def test_resolve_flux_uses_png_image_url_ref() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_FLUX_PAID_MODEL,
        user_prompt="закат в Париже",
        reference_data_url=_TG_URL,
        user_intent_en="sunset in paris",
    )
    assert "strictly as character identity reference" in prompt.lower()
    assert "override the camera distance" in prompt.lower()
    assert refs == [{"type": "image_url", "image_url": {"url": _PNG_DATA_URL}}]
    assert extras == {}


@pytest.mark.asyncio
async def test_resolve_nano_google_prompt_and_png_ref() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_NANO_BANANA2_MODEL,
        user_prompt="закат",
        reference_data_url=_TG_URL,
        user_intent_en="sunset in paris",
    )
    assert "character identity reference" in prompt.lower()
    assert "override the camera distance" in prompt.lower()
    assert "[Negative prompt:" in prompt
    assert refs == [{"type": "image_url", "image_url": {"url": _PNG_DATA_URL}}]
    assert extras == {}


@pytest.mark.asyncio
async def test_resolve_gemini_fallback_keeps_png_ref() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model="google/gemini-3-pro-image-preview",
        user_prompt="portrait",
        reference_input_url=_PNG_DATA_URL,
        user_intent_en="portrait in studio",
    )
    assert refs == [{"type": "image_url", "image_url": {"url": _PNG_DATA_URL}}]
    assert extras == {}
    assert "character identity reference" in prompt


@pytest.mark.asyncio
async def test_generate_openrouter_photo_flux_png_ref_in_body() -> None:
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
    assert body["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "negative_prompt" not in body
    assert "identity" not in body


@pytest.mark.asyncio
async def test_generate_openrouter_photo_fallback_preserves_png_ref() -> None:
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
    assert second_body["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "identity" not in second_body
    assert "negative_prompt" not in second_body


@pytest.mark.asyncio
async def test_resolve_gpt_image2_inpaint_with_refs() -> None:
    settings = Settings(tg_token="t", openrouter_key="test-key")
    prompt, refs, extras = await resolve_openrouter_photo_prompt_and_refs(
        settings,
        model=OPENROUTER_GPT_IMAGE2_MODEL,
        user_prompt="studio headshot",
        reference_data_url=_TG_URL,
        user_intent_en="studio headshot",
    )
    assert refs == [{"type": "image_url", "image_url": {"url": _PNG_DATA_URL}}]
    assert extras == {}
    assert "Inpaint and seamlessly integrate" in prompt
    assert "studio headshot" in prompt


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
async def test_reference_url_to_png_data_url_from_jpeg_bytes() -> None:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(120, 80, 40)).save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()
    with patch(
        "services.streaming_download.stream_download_to_bytes",
        AsyncMock(return_value=jpeg_bytes),
    ):
        data_url = await reference_url_to_png_data_url("https://cdn.example.com/face.jpg")
    assert data_url.startswith("data:image/png;base64,")


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
            input_references=[openrouter_input_reference(_PNG_DATA_URL)],
        )

    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"][0]["type"] == "image_url"
    assert "identity" not in body
    assert "negative_prompt" not in body
