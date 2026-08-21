"""OpenRouter Video API — оживление фото."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from config import Settings
from services.api_resilience import ExternalApiError
from services.openrouter_videos import (
    build_frame_images,
    generate_openrouter_animate_video,
)


def test_build_frame_images_maps_cookbook_first_frame_url() -> None:
    payload = build_frame_images("https://cdn.example/photo.jpg")
    assert payload == [{"first_frame": "https://cdn.example/photo.jpg"}]


@pytest.mark.asyncio
async def test_generate_openrouter_animate_video_polls_until_completed() -> None:
    settings = Settings(tg_token="bot-token", openrouter_key="or-key")
    bot = MagicMock()

    submit_resp = MagicMock()
    submit_resp.status_code = 202
    submit_resp.text = ""
    submit_resp.json.return_value = {
        "id": "job-1",
        "status": "pending",
        "polling_url": "/api/v1/videos/job-1",
    }

    poll_pending = MagicMock()
    poll_pending.status_code = 200
    poll_pending.text = ""
    poll_pending.json.return_value = {"id": "job-1", "status": "in_progress"}

    poll_done = MagicMock()
    poll_done.status_code = 200
    poll_done.text = ""
    poll_done.json.return_value = {
        "id": "job-1",
        "status": "completed",
        "unsigned_urls": ["https://cdn.openrouter.ai/video.mp4"],
    }

    client = MagicMock()
    client.post = AsyncMock(return_value=submit_resp)
    client.get = AsyncMock(side_effect=[poll_pending, poll_done])

    with (
        patch(
            "services.openrouter_videos.telegram_photo_download_url",
            AsyncMock(return_value="https://api.telegram.org/file/bot/photo.jpg"),
        ),
        patch(
            "services.openrouter_http.get_openrouter_http_client",
            AsyncMock(return_value=client),
        ),
        patch("services.openrouter_videos.asyncio.sleep", AsyncMock()),
    ):
        url = await generate_openrouter_animate_video(
            settings,
            bot=bot,
            telegram_file_id="AgAC_photo",
        )

    assert url == "https://cdn.openrouter.ai/video.mp4"
    client.post.assert_awaited_once()
    body = client.post.await_args.kwargs["json"]
    assert body["model"] == "bytedance/seedance-2.0-mini"
    assert body["frame_images"] == [{"first_frame": "https://api.telegram.org/file/bot/photo.jpg"}]
    assert "realistic eyes blinking" in body["prompt"]


@pytest.mark.asyncio
async def test_generate_openrouter_animate_video_402_raises_quota_error() -> None:
    settings = Settings(tg_token="bot-token", openrouter_key="or-key")
    bot = MagicMock()

    fail_resp = MagicMock()
    fail_resp.status_code = 402
    fail_resp.text = '{"error":{"message":"Insufficient credits"}}'

    client = MagicMock()
    client.post = AsyncMock(return_value=fail_resp)

    with (
        patch(
            "services.openrouter_videos.telegram_photo_download_url",
            AsyncMock(return_value="https://api.telegram.org/file/bot/photo.jpg"),
        ),
        patch(
            "services.openrouter_http.get_openrouter_http_client",
            AsyncMock(return_value=client),
        ),
    ):
        with pytest.raises(ExternalApiError, match="HTTP 402"):
            await generate_openrouter_animate_video(
                settings,
                bot=bot,
                telegram_file_id="AgAC_photo",
            )
