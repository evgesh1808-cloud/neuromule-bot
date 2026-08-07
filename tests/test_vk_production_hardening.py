"""Тесты vk_plain_text, vk_api_retry, vk_reference_store."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.vk_api_retry import is_vk_flood_error, vk_api_call_with_retry
from services.vk_plain_text import vk_plain_text
from services.vk_reference_store import (
    cache_vk_photo_from_url,
    clear_pending_vk_photo,
    take_pending_vk_photo,
)


def test_vk_plain_text_strips_html_and_markdown() -> None:
    raw = "📎 Фото принято. Теперь отправь <b>текстовый промпт</b> — **жирный** _курсив_."
    plain = vk_plain_text(raw)
    assert "<b>" not in plain
    assert "**" not in plain
    assert "текстовый промпт" in plain
    assert "жирный" in plain


def test_sanitize_telegram_plain_text_reexported() -> None:
    from services import vk_plain_text as mod

    assert mod.sanitize_telegram_plain_text("<b>x</b>") == "x"


def test_is_vk_flood_error_detects_codes() -> None:
    class _Err(Exception):
        error_code = 6

    assert is_vk_flood_error(_Err("Too many requests per second"))


@pytest.mark.asyncio
async def test_vk_api_call_with_retry_retries_flood() -> None:
    calls = {"n": 0}

    async def _flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Too many requests per second")
        return "ok"

    with patch("services.vk_api_retry.asyncio.sleep", new_callable=AsyncMock):
        result = await vk_api_call_with_retry(_flaky, max_attempts=5, base_delay_sec=0.01)

    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_cache_vk_photo_from_url_stores_bytes_by_peer() -> None:
    clear_pending_vk_photo(4242)
    fake_bytes = b"\xff\xd8\xff" + b"x" * 32

    with patch(
        "services.vk_reference_store.stream_download_to_bytes",
        new_callable=AsyncMock,
        return_value=fake_bytes,
    ):
        ref = await cache_vk_photo_from_url(4242, "https://cdn.example/photo.jpg", ttl_sec=60)

    assert ref is not None
    assert ref.data == fake_bytes
    taken = take_pending_vk_photo(4242)
    assert taken is not None
    assert taken.data == fake_bytes


@pytest.mark.asyncio
async def test_pending_vk_photo_expires() -> None:
    clear_pending_vk_photo(777)
    fake_bytes = b"img"

    with patch(
        "services.vk_reference_store.stream_download_to_bytes",
        new_callable=AsyncMock,
        return_value=fake_bytes,
    ):
        ref = await cache_vk_photo_from_url(777, "https://cdn.example/a.jpg", ttl_sec=0.01)

    assert ref is not None
    await asyncio.sleep(0.02)
    assert take_pending_vk_photo(777) is None
