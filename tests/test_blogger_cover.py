"""Тесты сервиса AI-обложки блогера."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import blogger_post_cache
from services.blogger_cover import (
    BloggerCoverOutcome,
    CoverIntegrationType,
    OPENROUTER_COVER_MODEL_ID,
    extract_image_prompt_from_draft,
    prepare_blogger_flux_prompt,
    resolve_blogger_draft,
    run_blogger_cover_turn,
)
from services.blogger_post_parser import MISSING_SECTION_PLACEHOLDER
from services.gemini_image_client import GeminiImageResult

_SAMPLE = """===ХУКИ===
Хук

===ТЕЛО ПОСТА===
Тело

===ПРИЗЫВЫ К ДЕЙСТВИЮ===
CTA

===ХЭШТЕГИ===
#AI

===ПРОМПТ ДЛЯ КАРТИНКИ===
A professional cinematic photo of sunset, 4k --ar 16:9
"""


@pytest.fixture(autouse=True)
def _clear_blogger_post_cache() -> None:
    blogger_post_cache._BY_ID.clear()
    blogger_post_cache._LAST_BY_USER.clear()
    blogger_post_cache._BY_MESSAGE.clear()
    yield
    blogger_post_cache._BY_ID.clear()
    blogger_post_cache._LAST_BY_USER.clear()
    blogger_post_cache._BY_MESSAGE.clear()


def test_extract_image_prompt_from_draft() -> None:
    post_id = blogger_post_cache.remember(42, _SAMPLE)
    draft = blogger_post_cache.get(post_id, 42)
    assert draft is not None
    assert extract_image_prompt_from_draft(draft) == draft.parsed.image_prompt


def test_extract_image_prompt_rejects_placeholder() -> None:
    raw = _SAMPLE.replace(
        "A professional cinematic photo of sunset, 4k --ar 16:9",
        MISSING_SECTION_PLACEHOLDER,
    )
    post_id = blogger_post_cache.remember(42, raw)
    draft = blogger_post_cache.get(post_id, 42)
    assert draft is not None
    assert extract_image_prompt_from_draft(draft) is None


def test_resolve_blogger_draft_by_last_session() -> None:
    post_id = blogger_post_cache.remember(77, _SAMPLE)
    draft = resolve_blogger_draft(77)
    assert draft is not None
    assert draft.post_id == post_id


def test_prepare_blogger_flux_prompt_keeps_english_template() -> None:
    raw = (
        "High-end editorial lifestyle photography of an office desk setup, "
        "soft dramatic lighting, shot on 35mm lens --ar 16:9"
    )
    out = prepare_blogger_flux_prompt(raw)
    assert "office desk" in out.lower()
    assert "high-end editorial" in out.lower()
    assert "--ar" not in out.lower()


@pytest.mark.asyncio
async def test_get_public_url_from_telegram_builds_api_telegram_url() -> None:
    from services.blogger_cover import get_public_url_from_telegram

    mock_bot = AsyncMock()
    mock_bot.get_file = AsyncMock(
        return_value=type("F", (), {"file_path": "photos/file_0.jpg"})()
    )

    url = await get_public_url_from_telegram(mock_bot, "AgACAgIAAxk", "123:ABC")

    assert url == "https://api.telegram.org/file/bot123:ABC/photos/file_0.jpg"
    mock_bot.get_file.assert_awaited_once_with("AgACAgIAAxk")


@pytest.mark.asyncio
async def test_generate_blogger_cover_image_uses_openrouter_flux() -> None:
    from config import Settings
    from services.blogger_cover import generate_blogger_cover_image

    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/cover.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        result = await generate_blogger_cover_image(
            settings,
            "A professional cinematic photo of desk",
        )

    assert result.url == "https://cdn.example.com/cover.webp"
    mock_client.post.assert_awaited_once()
    kwargs = mock_client.post.await_args.kwargs
    assert kwargs["json"]["model"] == OPENROUTER_COVER_MODEL_ID
    assert kwargs["json"]["aspect_ratio"] == "16:9"
    assert "input_references" not in kwargs["json"]
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_generate_blogger_cover_image_face_adds_reference_and_suffix() -> None:
    from config import Settings
    from services.blogger_cover import generate_blogger_cover_image

    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/face-cover.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        result = await generate_blogger_cover_image(
            settings,
            "A professional cinematic photo of desk",
            integration=CoverIntegrationType.FACE,
            source_file_url="https://api.telegram.org/file/bot/x/face.jpg",
        )

    assert result.url == "https://cdn.example.com/face-cover.webp"
    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"] == ["https://api.telegram.org/file/bot/x/face.jpg"]
    assert "seamlessly integrating the face" in body["prompt"]


@pytest.mark.asyncio
async def test_generate_blogger_cover_image_raises_on_http_error() -> None:
    from config import Settings
    from services.blogger_cover import generate_blogger_cover_image

    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 502
    mock_response.text = "bad gateway"
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch(
            "services.openrouter_http.get_openrouter_http_client",
            AsyncMock(return_value=mock_client),
        ),
        pytest.raises(RuntimeError, match="OpenRouter images HTTP 502"),
    ):
        await generate_blogger_cover_image(settings, "desk scene")


def test_prepare_blogger_flux_prompt_with_face_adds_portrait_hint() -> None:
    raw = (
        "High-end editorial lifestyle photography of an office desk setup, "
        "soft dramatic lighting --ar 16:9"
    )
    out = prepare_blogger_flux_prompt(raw, with_face=True)
    assert "portrait of a person" in out.lower()
    assert "central subject" in out.lower()


@pytest.mark.asyncio
async def test_run_blogger_cover_turn_success() -> None:
    post_id = blogger_post_cache.remember(100, _SAMPLE)
    draft = blogger_post_cache.get(post_id, 100)
    assert draft is not None

    mock_spend = AsyncMock(
        return_value=type(
            "Spend",
            (),
            {"ok": True, "charge": type("C", (), {"charge_id": "c1"})()},
        )()
    )
    mock_image = GeminiImageResult(url="https://cdn.example.com/art.png")

    with (
        patch("services.billing.blogger_pipeline.can_afford_blogger_cover", AsyncMock(return_value=True)),
        patch("services.billing.blogger_pipeline.spend_blogger_cover", mock_spend),
        patch(
            "services.blogger_cover.generate_blogger_cover_image",
            AsyncMock(return_value=mock_image),
        ),
        patch("services.blogger_cover.openrouter_cover_configured", return_value=True),
    ):
        from config import Settings

        result = await run_blogger_cover_turn(
            Settings(tg_token="t", openrouter_key="k"),
            user_id=100,
            draft=draft,
        )

    assert result.outcome is BloggerCoverOutcome.SUCCESS
    assert result.cleaned_prompt is not None
    assert "sunset" in result.cleaned_prompt.lower()
    assert result.image is not None
    assert result.image.url == "https://cdn.example.com/art.png"


@pytest.mark.asyncio
async def test_run_blogger_cover_turn_refunds_on_openrouter_error() -> None:
    post_id = blogger_post_cache.remember(102, _SAMPLE)
    draft = blogger_post_cache.get(post_id, 102)
    assert draft is not None

    mock_spend = AsyncMock(
        return_value=type(
            "Spend",
            (),
            {"ok": True, "charge": type("C", (), {"charge_id": "c-refund"})()},
        )()
    )
    mock_refund = AsyncMock()

    with (
        patch("services.billing.blogger_pipeline.can_afford_blogger_cover", AsyncMock(return_value=True)),
        patch("services.billing.blogger_pipeline.spend_blogger_cover", mock_spend),
        patch("services.billing.refund_charge", mock_refund),
        patch(
            "services.blogger_cover.generate_blogger_cover_image",
            AsyncMock(side_effect=RuntimeError("OpenRouter down")),
        ),
        patch("services.blogger_cover.openrouter_cover_configured", return_value=True),
    ):
        from config import Settings

        result = await run_blogger_cover_turn(
            Settings(tg_token="t", openrouter_key="k"),
            user_id=102,
            draft=draft,
        )

    assert result.outcome is BloggerCoverOutcome.GENERATION_FAILED
    mock_refund.assert_awaited_once_with("c-refund")


@pytest.mark.asyncio
async def test_run_blogger_cover_turn_missing_prompt() -> None:
    raw = _SAMPLE.replace(
        "A professional cinematic photo of sunset, 4k --ar 16:9",
        "",
    )
    post_id = blogger_post_cache.remember(101, raw)
    draft = blogger_post_cache.get(post_id, 101)
    assert draft is not None

    from config import Settings

    result = await run_blogger_cover_turn(
        Settings(tg_token="t", openrouter_key="k"),
        user_id=101,
        draft=draft,
    )
    assert result.outcome is BloggerCoverOutcome.PROMPT_NOT_FOUND
