"""Тесты сервиса AI-обложки блогера (очередь + OpenRouter)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import blogger_post_cache
from services.blogger_cover import (
    BloggerCoverOutcome,
    CoverIntegrationType,
    OPENROUTER_COVER_MODEL_ID,
    cover_generation_queue,
    extract_image_prompt_from_draft,
    prepare_blogger_flux_prompt,
    resolve_blogger_draft,
    run_blogger_cover_turn,
    stop_cover_queue_worker_for_tests,
    _process_cover_task,
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
async def _clear_blogger_cover_state() -> None:
    blogger_post_cache._BY_ID.clear()
    blogger_post_cache._LAST_BY_USER.clear()
    blogger_post_cache._BY_MESSAGE.clear()
    await stop_cover_queue_worker_for_tests()
    yield
    await stop_cover_queue_worker_for_tests()
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
    body = mock_client.post.await_args.kwargs["json"]
    assert body["model"] == OPENROUTER_COVER_MODEL_ID
    assert body["aspect_ratio"] == "16:9"
    assert "input_references" not in body


@pytest.mark.asyncio
async def test_generate_blogger_cover_image_face_uses_data_url_reference() -> None:
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
    data_url = "data:image/jpeg;base64,/9j/4AAQ"

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        result = await generate_blogger_cover_image(
            settings,
            "A professional cinematic photo of desk",
            integration=CoverIntegrationType.FACE,
            source_base64_url=data_url,
        )

    assert result.url == "https://cdn.example.com/face-cover.webp"
    body = mock_client.post.await_args.kwargs["json"]
    assert body["input_references"] == [
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    assert "seamlessly integrating the face" in body["prompt"]


@pytest.mark.asyncio
async def test_generate_blogger_cover_downloads_telegram_photo_as_data_url() -> None:
    from config import Settings
    from services.blogger_cover import generate_blogger_cover_image

    settings = Settings(tg_token="t", openrouter_key="test-key")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"url": "https://cdn.example.com/obj.webp"}],
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    mock_bot = AsyncMock()
    mock_bot.get_file = AsyncMock(
        return_value=type("F", (), {"file_path": "photos/product.jpg"})()
    )

    async def _fake_download(path: str, destination: Any = None, **_: Any) -> Any:
        destination.write(b"\xff\xd8\xfffakejpeg")
        return destination

    mock_bot.download_file = AsyncMock(side_effect=_fake_download)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=mock_client),
    ):
        result = await generate_blogger_cover_image(
            settings,
            "product on desk",
            integration=CoverIntegrationType.OBJECT,
            photo_file_id="AgAC_product",
            bot=mock_bot,
        )

    assert result.url == "https://cdn.example.com/obj.webp"
    ref = mock_client.post.await_args.kwargs["json"]["input_references"][0]
    assert ref["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_prepare_blogger_flux_prompt_with_face_adds_portrait_hint() -> None:
    raw = (
        "High-end editorial lifestyle photography of an office desk setup, "
        "soft dramatic lighting --ar 16:9"
    )
    out = prepare_blogger_flux_prompt(raw, with_face=True)
    assert "portrait of a person" in out.lower()


@pytest.mark.asyncio
async def test_run_blogger_cover_turn_enqueues_without_spend() -> None:
    post_id = blogger_post_cache.remember(100, _SAMPLE)
    draft = blogger_post_cache.get(post_id, 100)
    assert draft is not None

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=4242))
    mock_spend = AsyncMock()

    with (
        patch("services.billing.blogger_pipeline.can_afford_blogger_cover", AsyncMock(return_value=True)),
        patch("services.billing.blogger_pipeline.spend_blogger_cover", mock_spend),
        patch("services.blogger_cover.openrouter_cover_configured", return_value=True),
        patch("services.blogger_cover.start_cover_queue_worker", AsyncMock()),
    ):
        from config import Settings

        result = await run_blogger_cover_turn(
            Settings(tg_token="t", openrouter_key="k"),
            user_id=100,
            draft=draft,
            bot=mock_bot,
            chat_id=100,
        )

    assert result.outcome is BloggerCoverOutcome.QUEUED
    assert result.cleaned_prompt is not None
    assert "sunset" in result.cleaned_prompt.lower()
    mock_spend.assert_not_awaited()
    mock_bot.send_message.assert_awaited_once()
    assert cover_generation_queue.qsize() == 1
    task = cover_generation_queue.get_nowait()
    cover_generation_queue.task_done()
    assert task["user_id"] == 100
    assert task["integration"] == "none"
    assert task["status_message_id"] == 4242


@pytest.mark.asyncio
async def test_process_cover_task_refunds_on_openrouter_error() -> None:
    mock_spend = AsyncMock(
        return_value=type(
            "Spend",
            (),
            {"ok": True, "charge": type("C", (), {"charge_id": "c-refund"})()},
        )()
    )
    mock_refund = AsyncMock()
    mock_bot = AsyncMock()

    with (
        patch("services.billing.blogger_pipeline.spend_blogger_cover", mock_spend),
        patch("services.billing.refund_charge", mock_refund),
        patch(
            "services.blogger_cover.generate_blogger_cover_image",
            AsyncMock(side_effect=RuntimeError("OpenRouter down")),
        ),
        patch("services.blogger_cover._safe_send_text", AsyncMock()) as mock_text,
        patch("services.god_mode.billing_bypass", return_value=False),
    ):
        from config import Settings

        await _process_cover_task(
            {
                "settings": Settings(tg_token="t", openrouter_key="k"),
                "bot": mock_bot,
                "user_id": 102,
                "chat_id": 102,
                "post_id": "p1",
                "cleaned_prompt": "desk",
                "integration": "none",
                "photo_file_id": None,
                "status_message_id": 77,
            }
        )

    mock_refund.assert_awaited_once_with("c-refund")
    mock_text.assert_awaited()
    mock_bot.delete_message.assert_awaited_once_with(chat_id=102, message_id=77)


@pytest.mark.asyncio
async def test_process_cover_task_deletes_status_on_success() -> None:
    mock_spend = AsyncMock(
        return_value=type(
            "Spend",
            (),
            {"ok": True, "charge": type("C", (), {"charge_id": "c-ok"})()},
        )()
    )
    mock_bot = AsyncMock()

    with (
        patch("services.billing.blogger_pipeline.spend_blogger_cover", mock_spend),
        patch(
            "services.blogger_cover.generate_blogger_cover_image",
            AsyncMock(return_value=GeminiImageResult(data=b"webp")),
        ),
        patch("services.blogger_cover._safe_send_cover_photo", AsyncMock()),
        patch("services.god_mode.billing_bypass", return_value=False),
    ):
        from config import Settings

        await _process_cover_task(
            {
                "settings": Settings(tg_token="t", openrouter_key="k"),
                "bot": mock_bot,
                "user_id": 103,
                "chat_id": 103,
                "post_id": "p2",
                "cleaned_prompt": "desk",
                "integration": "none",
                "photo_file_id": None,
                "status_message_id": 88,
            }
        )

    mock_bot.delete_message.assert_awaited_once_with(chat_id=103, message_id=88)


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
        bot=AsyncMock(),
    )
    assert result.outcome is BloggerCoverOutcome.PROMPT_NOT_FOUND
