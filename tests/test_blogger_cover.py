"""Тесты сервиса AI-обложки блогера."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import blogger_post_cache
from services.blogger_cover import (
    BloggerCoverOutcome,
    FLUX_SCHNELL_MODEL_ID,
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
    raw = "A professional cinematic photo of office desk, 4k --ar 16:9"
    out = prepare_blogger_flux_prompt(raw)
    assert "office desk" in out.lower()
    assert out.startswith("A professional cinematic photo of")
    assert "--ar 16:9" in out


@pytest.mark.asyncio
async def test_generate_blogger_cover_image_uses_flux_schnell() -> None:
    from config import settings
    from services.blogger_cover import generate_blogger_cover_image

    with patch(
        "services.replicate_client.call_replicate_model",
        AsyncMock(return_value="https://cdn.example.com/cover.webp"),
    ) as mock_replicate:
        result = await generate_blogger_cover_image(settings, "A professional cinematic photo of desk")

    assert result.url == "https://cdn.example.com/cover.webp"
    mock_replicate.assert_awaited_once()
    assert mock_replicate.await_args.args[0] == FLUX_SCHNELL_MODEL_ID
    assert mock_replicate.await_args.args[1]["aspect_ratio"] == "16:9"


@pytest.mark.asyncio
async def test_generate_blogger_cover_image_with_face_runs_swap() -> None:
    from config import settings
    from services.blogger_cover import generate_blogger_cover_image

    mock_bot = AsyncMock()
    with (
        patch(
            "services.replicate_client.call_replicate_model",
            AsyncMock(side_effect=["https://cdn.example.com/base.webp", "https://cdn.example.com/swapped.webp"]),
        ) as mock_replicate,
        patch(
            "services.replicate_client.telegram_photo_download_url",
            AsyncMock(return_value="https://api.telegram.org/file/bot/x/face.jpg"),
        ),
    ):
        result = await generate_blogger_cover_image(
            settings,
            "A professional cinematic photo of desk",
            face_file_id="face123",
            bot=mock_bot,
        )

    assert result.url == "https://cdn.example.com/swapped.webp"
    assert mock_replicate.await_count == 2
    assert mock_replicate.await_args_list[1].args[1]["swap_image"] == "https://api.telegram.org/file/bot/x/face.jpg"


def test_prepare_blogger_flux_prompt_with_face_adds_portrait_hint() -> None:
    raw = "A professional cinematic photo of office desk, 4k --ar 16:9"
    out = prepare_blogger_flux_prompt(raw, with_face=True)
    assert "portrait photo of a person" in out.lower()


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
    ):
        from config import settings

        result = await run_blogger_cover_turn(settings, user_id=100, draft=draft)

    assert result.outcome is BloggerCoverOutcome.SUCCESS
    assert result.cleaned_prompt is not None
    assert "sunset" in result.cleaned_prompt.lower()
    assert result.image is not None
    assert result.image.url == "https://cdn.example.com/art.png"


@pytest.mark.asyncio
async def test_run_blogger_cover_turn_missing_prompt() -> None:
    raw = _SAMPLE.replace(
        "A professional cinematic photo of sunset, 4k --ar 16:9",
        "",
    )
    post_id = blogger_post_cache.remember(101, raw)
    draft = blogger_post_cache.get(post_id, 101)
    assert draft is not None

    from config import settings

    result = await run_blogger_cover_turn(settings, user_id=101, draft=draft)
    assert result.outcome is BloggerCoverOutcome.PROMPT_NOT_FOUND
