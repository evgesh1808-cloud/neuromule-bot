"""Тесты интерактивного flow AI-обложки блогера (фото лица)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from content.inline_keyboards import get_blogger_cover_face_keyboard
from services import blogger_post_cache
from services.repository import has_blogger_face_photo, set_blogger_face_file_id

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


def test_blogger_cover_face_keyboard_buttons() -> None:
    post_id = "abc123"
    markup = get_blogger_cover_face_keyboard(post_id)
    flat = [btn for row in markup.inline_keyboard for btn in row]
    assert len(flat) == 2
    assert flat[0].text == "📸 Загрузить фото"
    assert flat[0].callback_data == f"{msg.CB_BLOGGER_COVER_UPLOAD_FACE_PREFIX}{post_id}"
    assert flat[1].text == "🖼️ Создать без фото"
    assert flat[1].callback_data == f"{msg.CB_BLOGGER_COVER_NO_FACE_PREFIX}{post_id}"


@pytest.mark.asyncio
async def test_has_blogger_face_photo_after_save(repo_module) -> None:
    uid = 990_001
    assert await has_blogger_face_photo(uid) is False
    await set_blogger_face_file_id(uid, "AgACAgIAAxkBphoto")
    assert await has_blogger_face_photo(uid) is True


@pytest.mark.asyncio
async def test_cb_blogger_cover_art_prompts_face_choice_when_no_photo() -> None:
    from platforms.blogger_flow import cb_blogger_cover_art

    post_id = blogger_post_cache.remember(501, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 501
    callback.data = f"{msg.CB_BLOGGER_COVER_PREFIX}{post_id}"
    callback.message.chat.id = 1
    callback.message.message_id = 10
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    with patch("platforms.blogger_flow.has_blogger_face_photo", AsyncMock(return_value=False)):
        await cb_blogger_cover_art(callback)

    callback.answer.assert_awaited_once()
    callback.message.answer.assert_awaited_once()
    args, kwargs = callback.message.answer.await_args
    assert args[0] == msg.TXT_BLOGGER_COVER_FACE_CHOICE
    assert kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_cb_blogger_cover_art_starts_generation_when_face_exists() -> None:
    from platforms.blogger_flow import cb_blogger_cover_art

    post_id = blogger_post_cache.remember(502, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 502
    callback.data = f"{msg.CB_BLOGGER_COVER_PREFIX}{post_id}"
    callback.message.chat.id = 2
    callback.message.message_id = 11
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()

    with (
        patch("platforms.blogger_flow.has_blogger_face_photo", AsyncMock(return_value=True)),
        patch(
            "platforms.blogger_flow.handle_blogger_cover_callback",
            AsyncMock(),
        ) as mock_handle,
    ):
        await cb_blogger_cover_art(callback)

    mock_handle.assert_awaited_once()
    assert mock_handle.await_args.kwargs["use_face"] is True
