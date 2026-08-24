"""OpenRouter Video API — оживление фото."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Settings
from services.api_resilience import ExternalApiError
from services.openrouter_videos import (
    OpenRouterAnimateResult,
    build_frame_images,
    generate_openrouter_animate_video,
    photo_ref_to_data_url,
    resolve_animate_duration_for_model,
)


def test_resolve_animate_duration_for_veo() -> None:
    assert resolve_animate_duration_for_model("google/veo-3.1-lite") == 4
    assert resolve_animate_duration_for_model("bytedance/seedance-2.0-mini") == 4


def test_build_frame_images_uses_openrouter_content_part_schema() -> None:
    payload = build_frame_images("https://cdn.example/photo.jpg")
    assert payload == [
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example/photo.jpg"},
            "frame_type": "first_frame",
        }
    ]


@pytest.mark.asyncio
async def test_photo_ref_to_data_url_from_telegram_file_id() -> None:
    bot = MagicMock()
    tg_file = MagicMock()
    tg_file.file_path = "photos/file.jpg"
    bot.get_file = AsyncMock(return_value=tg_file)
    bot.download_file = AsyncMock(side_effect=lambda _path, dest: dest.write(b"jpeg-bytes"))

    data_url = await photo_ref_to_data_url(
        Settings(tg_token="bot-token"),
        bot,
        "AgAC_photo",
    )

    assert data_url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_generate_openrouter_animate_video_polls_until_completed() -> None:
    settings = Settings(tg_token="bot-token", openrouter_key="or-key")
    bot = MagicMock()
    tg_file = MagicMock()
    tg_file.file_path = "photos/file.jpg"
    bot.get_file = AsyncMock(return_value=tg_file)
    bot.download_file = AsyncMock(side_effect=lambda _path, dest: dest.write(b"jpeg-bytes"))

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
            "services.openrouter_http.get_openrouter_http_client",
            AsyncMock(return_value=client),
        ),
        patch("services.openrouter_videos.asyncio.sleep", AsyncMock()),
    ):
        result = await generate_openrouter_animate_video(
            settings,
            bot=bot,
            telegram_file_id="AgAC_photo",
        )

    assert result == OpenRouterAnimateResult(
        url="https://cdn.openrouter.ai/video.mp4",
        api_key="or-key",
    )
    client.post.assert_awaited_once()
    body = client.post.await_args.kwargs["json"]
    assert body["model"] == "bytedance/seedance-2.0-mini"
    assert body["duration"] == 4
    frame = body["frame_images"][0]
    assert frame["type"] == "image_url"
    assert frame["frame_type"] == "first_frame"
    assert frame["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "NO yawning" in body["prompt"]
    assert "NO smiling" in body["prompt"]


@pytest.mark.asyncio
async def test_generate_openrouter_animate_video_retries_with_data_url_on_image_error() -> None:
    settings = Settings(tg_token="bot-token", openrouter_key="or-key")
    bot = MagicMock()

    bad_resp = MagicMock()
    bad_resp.status_code = 400
    bad_resp.text = '{"error":{"message":"unable to retrieve image from url"}}'

    ok_resp = MagicMock()
    ok_resp.status_code = 202
    ok_resp.text = ""
    ok_resp.json.return_value = {
        "id": "job-2",
        "status": "pending",
        "polling_url": "/api/v1/videos/job-2",
    }

    poll_done = MagicMock()
    poll_done.status_code = 200
    poll_done.text = ""
    poll_done.json.return_value = {
        "id": "job-2",
        "status": "completed",
        "unsigned_urls": ["https://cdn.openrouter.ai/video2.mp4"],
    }

    client = MagicMock()
    client.post = AsyncMock(side_effect=[bad_resp, ok_resp])
    client.get = AsyncMock(return_value=poll_done)

    with (
        patch(
            "services.openrouter_videos.resolve_frame_image_url",
            AsyncMock(
                side_effect=[
                    "https://cdn.example/photo.jpg",
                    "data:image/jpeg;base64,abc",
                ]
            ),
        ),
        patch(
            "services.openrouter_http.get_openrouter_http_client",
            AsyncMock(return_value=client),
        ),
        patch("services.openrouter_videos.asyncio.sleep", AsyncMock()),
    ):
        result = await generate_openrouter_animate_video(
            settings,
            bot=bot,
            telegram_file_id="https://cdn.example/photo.jpg",
        )

    assert result.url == "https://cdn.openrouter.ai/video2.mp4"
    assert client.post.await_count == 2
    second_body = client.post.await_args_list[1].kwargs["json"]
    assert second_body["frame_images"][0]["image_url"]["url"].startswith("data:")


@pytest.mark.asyncio
async def test_generate_openrouter_animate_video_402_raises_quota_error() -> None:
    settings = Settings(tg_token="bot-token", openrouter_key="or-key")
    bot = MagicMock()
    tg_file = MagicMock()
    tg_file.file_path = "photos/file.jpg"
    bot.get_file = AsyncMock(return_value=tg_file)
    bot.download_file = AsyncMock(side_effect=lambda _path, dest: dest.write(b"jpeg-bytes"))

    fail_resp = MagicMock()
    fail_resp.status_code = 402
    fail_resp.text = '{"error":{"message":"Insufficient credits"}}'

    client = MagicMock()
    client.post = AsyncMock(return_value=fail_resp)

    with patch(
        "services.openrouter_http.get_openrouter_http_client",
        AsyncMock(return_value=client),
    ):
        with pytest.raises(ExternalApiError, match="HTTP 402"):
            await generate_openrouter_animate_video(
                settings,
                bot=bot,
                telegram_file_id="AgAC_photo",
            )
