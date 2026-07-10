"""Тесты сервиса AI-обложки блогера."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import blogger_post_cache
from services.blogger_cover import (
    BloggerCoverOutcome,
    _parse_openrouter_image_message,
    extract_image_prompt_from_draft,
    resolve_blogger_draft,
    run_blogger_cover_turn,
)
from services.blogger_post_parser import MISSING_SECTION_PLACEHOLDER, parse_blogger_post
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


def test_parse_openrouter_image_message_from_images_array() -> None:
    message = {
        "images": [
            {"image_url": {"url": "https://cdn.example.com/cover.png"}},
        ]
    }
    result = _parse_openrouter_image_message(message)
    assert result.url == "https://cdn.example.com/cover.png"


def test_parse_openrouter_image_message_from_base64_data_url() -> None:
    message = {
        "images": [
            {"image_url": {"url": "data:image/png;base64,QUJDRA=="}},
        ]
    }
    result = _parse_openrouter_image_message(message)
    assert result.data == b"ABCD"


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
        patch("services.blogger_cover.clean_blogger_cover_prompt", AsyncMock(return_value="clean prompt")),
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
    assert result.cleaned_prompt == "clean prompt"
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
