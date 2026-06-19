"""Тесты премодерации Галереи (approve_gal / reject_gal)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from content import messages as msg
from platforms.handlers import gallery_flow
from services import last_share_media


@pytest.fixture(autouse=True)
def _isolate_share_cache():
    last_share_media._BY_USER.clear()
    last_share_media._BY_TASK.clear()
    last_share_media._TS.clear()
    yield
    last_share_media._BY_USER.clear()
    last_share_media._BY_TASK.clear()
    last_share_media._TS.clear()


def _patch_settings(monkeypatch: pytest.MonkeyPatch, chat_id: int) -> None:
    """Подменяет `gallery_flow.app_settings` на безопасный SimpleNamespace.

    Прямой setattr на pydantic-инстанс Settings запрещён (frozen=True),
    поэтому подменяем сам модульный атрибут — этого достаточно, потому
    что хэндлер всюду читает `gallery_flow.app_settings.<field>`."""
    stub = SimpleNamespace(gallery_moderation_chat_id=chat_id)
    monkeypatch.setattr(gallery_flow, "app_settings", stub, raising=True)


def test_moderation_chat_configured_false_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, 0)
    assert gallery_flow._moderation_chat_configured() is False


def test_moderation_chat_configured_true_when_id_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, -100123)
    assert gallery_flow._moderation_chat_configured() is True


def test_moderation_keyboard_has_approve_and_reject_buttons() -> None:
    kb = gallery_flow._moderation_keyboard("task_abc")
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "approve_gal:task_abc" in callbacks
    assert "reject_gal:task_abc" in callbacks


def test_messages_have_required_constants() -> None:
    assert msg.CB_GALLERY_APPROVE_PREFIX == "approve_gal:"
    assert msg.CB_GALLERY_REJECT_PREFIX == "reject_gal:"
    assert "task_id" in msg.TXT_GALLERY_MODERATION_HEADER
    assert "NeuroMule" in msg.TXT_GALLERY_MOD_APPROVED_NOTIFY
    # У отклонения бренда нет в тексте — проверяем хвостовой эмодзи 🐎⚡️
    # (фирменный «копытный» подпись NeuroMule).
    assert "🐎⚡️" in msg.TXT_GALLERY_MOD_REJECTED_NOTIFY


@pytest.mark.asyncio
async def test_send_to_moderation_skipped_if_chat_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, 0)
    entry = last_share_media.ShareMediaEntry(
        user_id=10, task_id="t", task_type="photo", prompt="x", file_id="ph",
    )
    bot = SimpleNamespace(send_photo=AsyncMock())
    sent = await gallery_flow._send_to_moderation(entry, bot)
    assert sent is False
    bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_moderation_sends_photo_with_kb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, -100777)
    entry = last_share_media.ShareMediaEntry(
        user_id=10, task_id="task_99", task_type="photo", prompt="cyberpunk", file_id="ph_1",
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_video=AsyncMock(),
        send_audio=AsyncMock(),
        send_message=AsyncMock(),
    )
    ok = await gallery_flow._send_to_moderation(entry, bot)
    assert ok is True
    bot.send_photo.assert_awaited_once()
    _, kwargs = bot.send_photo.call_args
    assert "task_99" in kwargs["caption"]
    kb = kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "approve_gal:task_99" in callbacks
    assert "reject_gal:task_99" in callbacks


@pytest.mark.asyncio
async def test_send_to_moderation_for_video(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, -100777)
    entry = last_share_media.ShareMediaEntry(
        user_id=10, task_id="vt", task_type="video", prompt="cinematic", file_id="vid_x",
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_video=AsyncMock(),
        send_audio=AsyncMock(),
        send_message=AsyncMock(),
    )
    ok = await gallery_flow._send_to_moderation(entry, bot)
    assert ok is True
    bot.send_video.assert_awaited_once()
    bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_moderation_for_music_uses_performer_brand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, -100777)
    entry = last_share_media.ShareMediaEntry(
        user_id=10, task_id="mt", task_type="music", prompt="lofi", file_id="aud_x",
    )
    bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_video=AsyncMock(),
        send_audio=AsyncMock(),
        send_message=AsyncMock(),
    )
    ok = await gallery_flow._send_to_moderation(entry, bot)
    assert ok is True
    bot.send_audio.assert_awaited_once()
    _, kwargs = bot.send_audio.call_args
    assert kwargs["performer"] == "NeuroMule 🐎"
