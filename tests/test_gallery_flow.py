"""Тесты модуля Галереи NeuroMule 🐎⚡️.

Покрывают:
  • кэш ``last_share_media`` (remember / get_by_user / get_by_task / clear);
  • graceful-skip кросс-сервисов при пустой конфигурации (TG/VK/MAX);
  • формирование клавиатур (confirm card, виральный share-row) и
    наличие callback-констант ``share_to_gallery`` / ``confirm_gallery_publish``.
"""

from __future__ import annotations

import pytest

from types import SimpleNamespace

from content import messages as msg
from platforms.handlers.gallery_flow import (
    gallery_confirm_keyboard,
    gallery_share_row,
)
from services import (
    gallery_service,
    last_share_media,
    max_app_service,
    vk_gallery_service,
)


def _stub_settings(**overrides) -> SimpleNamespace:
    base = dict(
        gallery_channel_id="",
        vk_group_token="",
        vk_group_id=0,
        vk_photo_album_id=0,
        vk_video_album_id=0,
        vk_share_short_url="https://vk.cc/neuromule_bot",
        max_api_token="",
        max_api_url="https://maxapp.ru/api/v1/feed/upload",
        telegram_bot_username="NeuroMule_bot",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ─── last_share_media ───────────────────────────────────────────────────────


def test_remember_and_get_by_user_and_task() -> None:
    last_share_media.clear(42)
    entry = last_share_media.remember(
        user_id=42,
        task_id="task-abc",
        task_type="photo",
        prompt="cinematic horse on the moon",
        media_url="https://cdn.test/photo.png",
    )
    assert entry.task_id == "task-abc"
    by_user = last_share_media.get_by_user(42)
    by_task = last_share_media.get_by_task("task-abc")
    assert by_user is not None and by_user.task_id == "task-abc"
    assert by_task is not None and by_task.user_id == 42

    last_share_media.clear(42)
    assert last_share_media.get_by_user(42) is None
    assert last_share_media.get_by_task("task-abc") is None


def test_remember_rejects_empty_media() -> None:
    with pytest.raises(ValueError):
        last_share_media.remember(
            user_id=1,
            task_id="t1",
            task_type="music",
            prompt="x",
        )


# ─── keyboards ──────────────────────────────────────────────────────────────


def test_gallery_confirm_keyboard_has_two_buttons() -> None:
    kb = gallery_confirm_keyboard()
    flat = [b for row in kb.inline_keyboard for b in row]
    cbs = {b.callback_data for b in flat}
    assert msg.CB_GALLERY_CONFIRM in cbs
    assert msg.CB_GALLERY_CANCEL in cbs


def test_gallery_share_row_contains_share_and_inline_forward() -> None:
    row = gallery_share_row(task_id="abc")
    cbs = {b.callback_data for b in row}
    forwards = {b.switch_inline_query for b in row if b.switch_inline_query}
    assert msg.CB_SHARE_TO_GALLERY in cbs
    assert "get_media_abc" in forwards


def test_gallery_share_row_falls_back_to_last_token() -> None:
    row = gallery_share_row()
    forwards = {b.switch_inline_query for b in row if b.switch_inline_query}
    assert "get_media_last" in forwards


# ─── graceful skip cross-posting when not configured ──────────────────────


@pytest.fixture
def _isolated_xpost_config(monkeypatch):
    """Подменяем ``app_settings`` на namespace с пустыми токенами в каждом сервисе."""

    stub = _stub_settings()
    monkeypatch.setattr(gallery_service, "app_settings", stub)
    monkeypatch.setattr(vk_gallery_service, "app_settings", stub)
    monkeypatch.setattr(max_app_service, "app_settings", stub)
    yield stub


def test_configured_flags_return_false_without_env(_isolated_xpost_config) -> None:
    assert gallery_service.gallery_channel_configured() is False
    assert vk_gallery_service.vk_configured() is False
    assert max_app_service.max_app_configured() is False


@pytest.mark.asyncio
async def test_tg_gallery_skip_without_channel(_isolated_xpost_config) -> None:
    entry = last_share_media.ShareMediaEntry(
        user_id=1,
        task_id="t-tg",
        task_type="photo",
        prompt="test",
        media_url="https://x/y.png",
    )

    class _BotStub:
        async def send_photo(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("must not be called when channel is empty")

    assert await gallery_service.post_to_gallery_channel(_BotStub(), entry) is False


@pytest.mark.asyncio
async def test_vk_skip_without_token(_isolated_xpost_config) -> None:
    entry = last_share_media.ShareMediaEntry(
        user_id=2,
        task_id="t-vk",
        task_type="photo",
        prompt="test",
        media_url="https://x/y.png",
    )
    assert await vk_gallery_service.cross_post_to_vk(entry) is False


@pytest.mark.asyncio
async def test_max_app_skip_without_token(_isolated_xpost_config) -> None:
    entry = last_share_media.ShareMediaEntry(
        user_id=3,
        task_id="t-max",
        task_type="video",
        prompt="test",
        media_url="https://x/y.mp4",
    )
    assert await max_app_service.cross_post_to_max_app(entry) is False


@pytest.mark.asyncio
async def test_max_app_skips_non_video_kind(monkeypatch) -> None:
    # Даже если токен сконфигурен, не-видео контент пропускается.
    stub = _stub_settings(max_api_token="tok_dummy")
    monkeypatch.setattr(max_app_service, "app_settings", stub)
    entry = last_share_media.ShareMediaEntry(
        user_id=4,
        task_id="t-photo",
        task_type="photo",  # photo → пропуск (только video/animate/music)
        prompt="x",
        media_url="https://x/y.png",
    )
    assert await max_app_service.cross_post_to_max_app(entry) is False


# ─── messages constants sanity ─────────────────────────────────────────────


def test_gallery_hashtags_cover_all_task_types() -> None:
    assert msg.GALLERY_HASHTAGS["photo"] == "#gallery_flux"
    assert msg.GALLERY_HASHTAGS["video"] == "#studio_video"
    assert msg.GALLERY_HASHTAGS["animate"] == "#studio_video"
    assert msg.GALLERY_HASHTAGS["music"] == "#radio_suno"


def test_gallery_confirm_text_has_anonymity_guarantee() -> None:
    """Карточка подтверждения — лаконичная, только гарантия 100% анонимности
    профиля (имя, @username, Telegram ID) и финальный CTA на согласие."""

    text = msg.TXT_GALLERY_CONFIRM_TEXT
    assert "Гарантия анонимности" in text
    assert "имя" in text
    assert "@username" in text
    assert "Telegram ID" in text
    assert "скрытыми на 100%" in text
    assert "Подтверждаешь публикацию" in text


def test_gallery_confirm_text_is_link_free_and_offer_free() -> None:
    """Никаких гиперссылок / упоминаний Telegra.ph-документов в этом окне —
    юзер уже принял их при ``/start``. Интерфейс шеринга должен быть
    максимально быстрым и лаконичным для виральности."""

    text = msg.TXT_GALLERY_CONFIRM_TEXT.lower()
    assert "telegra.ph" not in text
    assert "http://" not in text
    assert "https://" not in text
    # И отсылок к оферте/политике/соглашению быть не должно.
    assert "оферт" not in text
    assert "политик" not in text
    assert "соглашен" not in text


def test_review_thanks_mentions_bonus() -> None:
    assert "+5 ⚡" in msg.TXT_REVIEW_THANKS
