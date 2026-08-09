"""Photo generation status UX — pure format + progress loop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from services.photo_gen_status import (
    PHOTO_STATUS_PROGRESS_DELAYS_SEC,
    format_photo_gen_status_html,
    photo_gen_eta_hint,
    photo_status_progress_scope,
    send_photo_gen_status_message,
    _photo_status_progress_loop,
)


def test_format_photo_gen_status_phases() -> None:
    t0 = format_photo_gen_status_html(model_label="Flux", aspect_ratio="1:1", phase=0)
    assert "Flux" in t0
    assert "1:1" in t0
    assert "можно закрыть чат" in t0

    t1 = format_photo_gen_status_html(model_label="Flux", aspect_ratio="9:16", phase=1)
    assert "9:16" in t1
    assert "рисую" in t1.lower()

    t2 = format_photo_gen_status_html(model_label="DALL·E", aspect_ratio="16:9", phase=2)
    assert "Финальные" in t2


def test_photo_gen_eta_hint_free_vs_paid() -> None:
    assert "1–3" in photo_gen_eta_hint(model_id="flux_schnell")
    assert "30–90" in photo_gen_eta_hint(model_id="nano_banana_pro")


@pytest.mark.asyncio
async def test_send_photo_gen_status_message() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    msg = await send_photo_gen_status_message(
        bot,
        1,
        model_label="Flux FREE",
        aspect_ratio="1:1",
        model_id="flux_schnell",
    )
    assert msg is not None
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_progress_loop_stops_on_event() -> None:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    stop = asyncio.Event()
    stop.set()
    await _photo_status_progress_loop(
        bot,
        1,
        99,
        model_label="Flux",
        aspect_ratio="1:1",
        eta_hint="1–3 мин",
        stop=stop,
    )
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_loop_edits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    stop = asyncio.Event()
    monkeypatch.setattr(
        "services.photo_gen_status.PHOTO_STATUS_PROGRESS_DELAYS_SEC",
        (0, 999),
    )

    async def _stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    asyncio.create_task(_stop_soon())
    await _photo_status_progress_loop(
        bot,
        1,
        99,
        model_label="Flux",
        aspect_ratio="1:1",
        eta_hint="1–3 мин",
        stop=stop,
    )
    assert bot.edit_message_text.await_count >= 1


@pytest.mark.asyncio
async def test_progress_scope_cancels_on_exit() -> None:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(
        side_effect=TelegramBadRequest(method=None, message="not found")
    )
    async with photo_status_progress_scope(
        bot,
        1,
        100,
        model_label="Flux",
        aspect_ratio="1:1",
        model_id="flux_schnell",
    ):
        await asyncio.sleep(0.01)
    # no leak — context exits cleanly


def test_progress_delays_bounded() -> None:
    assert len(PHOTO_STATUS_PROGRESS_DELAYS_SEC) <= 3
