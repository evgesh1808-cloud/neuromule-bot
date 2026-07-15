"""Тесты интерактивного flow AI-обложки блогера (форматы none/face/object)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.enums import ParseMode

from content import messages as msg
from content.inline_keyboards import (
    get_blogger_cover_face_keyboard,
    get_blogger_cover_options_keyboard,
)
from services import blogger_post_cache
from services.blogger_cover import parse_cover_generate
from services.repository import (
    has_blogger_face_photo,
    has_blogger_object_photo,
    set_blogger_face_file_id,
    set_blogger_object_file_id,
)

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


def test_blogger_cover_options_keyboard_layout() -> None:
    post_id = "abc123"
    markup = get_blogger_cover_options_keyboard(post_id)
    assert len(markup.inline_keyboard) == 4
    none_btn = markup.inline_keyboard[0][0]
    face_btn = markup.inline_keyboard[1][0]
    object_btn = markup.inline_keyboard[2][0]
    back_btn = markup.inline_keyboard[3][0]
    assert none_btn.text == msg.BTN_BLOGGER_COVER_MODE_NONE
    assert none_btn.callback_data == f"{msg.CB_COVER_GENERATE_PREFIX}none:{post_id}"
    assert face_btn.callback_data == f"{msg.CB_COVER_GENERATE_PREFIX}face:{post_id}"
    assert object_btn.callback_data == f"{msg.CB_COVER_GENERATE_PREFIX}object:{post_id}"
    assert back_btn.text == msg.BTN_BLOGGER_COVER_BACK
    assert back_btn.callback_data == f"{msg.CB_BLOG_BACK_PREFIX}{post_id}"


def test_parse_cover_generate() -> None:
    assert parse_cover_generate("cover_generate:none:deadbeef") == ("none", "deadbeef")
    assert parse_cover_generate("cover_generate:face:abc") == ("face", "abc")
    assert parse_cover_generate("cover_generate:object:xyz") == ("object", "xyz")
    assert parse_cover_generate("cover_generate:unknown:xyz") is None
    assert parse_cover_generate("blogger_cover:xyz") is None


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
async def test_has_blogger_object_photo_after_save(repo_module) -> None:
    uid = 990_002
    assert await has_blogger_object_photo(uid) is False
    await set_blogger_object_file_id(uid, "AgACAgIAAxkBobject")
    assert await has_blogger_object_photo(uid) is True


@pytest.mark.asyncio
async def test_cb_blogger_cover_art_opens_options_keyboard() -> None:
    from platforms.blogger_flow import cb_blogger_cover_art

    post_id = blogger_post_cache.remember(501, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 501
    callback.data = f"{msg.CB_BLOGGER_COVER_PREFIX}{post_id}"
    callback.message.chat.id = 1
    callback.message.message_id = 10
    callback.message.edit_reply_markup = AsyncMock()
    callback.answer = AsyncMock()

    await cb_blogger_cover_art(callback)

    callback.message.edit_reply_markup.assert_awaited_once()
    kwargs = callback.message.edit_reply_markup.await_args.kwargs
    markup = kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == (
        f"{msg.CB_COVER_GENERATE_PREFIX}none:{post_id}"
    )
    callback.answer.assert_awaited_once_with(msg.TXT_BLOGGER_COVER_OPTIONS)


@pytest.mark.asyncio
async def test_cb_blogger_cover_generate_none_starts_generation() -> None:
    from platforms.blogger_flow import cb_blogger_cover_generate

    post_id = blogger_post_cache.remember(502, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 502
    callback.data = f"{msg.CB_COVER_GENERATE_PREFIX}none:{post_id}"
    callback.message.chat.id = 2
    callback.message.message_id = 11
    callback.answer = AsyncMock()
    state = MagicMock()

    with patch(
        "platforms.blogger_flow.handle_blogger_cover_callback",
        AsyncMock(),
    ) as mock_handle:
        await cb_blogger_cover_generate(callback, state)

    mock_handle.assert_awaited_once()
    assert mock_handle.await_args.kwargs["use_face"] is False
    assert mock_handle.await_args.kwargs["use_object"] is False


@pytest.mark.asyncio
async def test_cb_blogger_cover_generate_face_asks_upload_when_missing() -> None:
    from platforms.blogger_flow import cb_blogger_cover_generate

    post_id = blogger_post_cache.remember(503, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 503
    callback.data = f"{msg.CB_COVER_GENERATE_PREFIX}face:{post_id}"
    callback.message.chat.id = 3
    callback.message.message_id = 12
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    with patch("platforms.blogger_flow.has_blogger_face_photo", AsyncMock(return_value=False)):
        await cb_blogger_cover_generate(callback, state)

    state.set_state.assert_awaited_once()
    callback.message.answer.assert_awaited_once()
    assert callback.message.answer.await_args.args[0] == msg.TXT_BLOGGER_COVER_UPLOAD_FACE_HINT


@pytest.mark.asyncio
async def test_cb_blogger_cover_generate_face_shows_reuse_keyboard_when_saved() -> None:
    from platforms.blogger_flow import cb_blogger_cover_generate

    post_id = blogger_post_cache.remember(513, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 513
    callback.data = f"{msg.CB_COVER_GENERATE_PREFIX}face:{post_id}"
    callback.message.chat.id = 13
    callback.message.message_id = 22
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()

    with (
        patch("platforms.blogger_flow.has_blogger_face_photo", AsyncMock(return_value=True)),
        patch(
            "platforms.blogger_flow.handle_blogger_cover_callback",
            AsyncMock(),
        ) as mock_handle,
    ):
        await cb_blogger_cover_generate(callback, state)

    mock_handle.assert_not_awaited()
    state.set_state.assert_not_awaited()
    callback.message.edit_reply_markup.assert_awaited_once()
    markup = callback.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    flat = [btn for row in markup.inline_keyboard for btn in row]
    assert any(btn.callback_data == f"{msg.CB_BLOGGER_FACE_USE_PREFIX}{post_id}" for btn in flat)
    assert any(btn.callback_data == f"{msg.CB_BLOGGER_FACE_NEW_PREFIX}{post_id}" for btn in flat)


@pytest.mark.asyncio
async def test_cb_blogger_face_upload_new_clears_file_id_and_sets_fsm() -> None:
    from platforms.blogger_flow import cb_blogger_face_upload_new
    from platforms.telegram_states import BloggerFlowStates

    post_id = blogger_post_cache.remember(514, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 514
    callback.data = f"{msg.CB_BLOGGER_FACE_NEW_PREFIX}{post_id}"
    callback.message.chat.id = 14
    callback.message.message_id = 23
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    with patch(
        "platforms.blogger_flow.set_blogger_face_file_id",
        AsyncMock(),
    ) as mock_clear:
        await cb_blogger_face_upload_new(callback, state)

    mock_clear.assert_awaited_once_with(514, "")
    state.set_state.assert_awaited_once_with(BloggerFlowStates.waiting_for_face_photo)
    callback.message.answer.assert_awaited_once_with(
        msg.TXT_BLOGGER_COVER_UPLOAD_FACE_HINT,
        parse_mode=ParseMode.HTML,
    )


@pytest.mark.asyncio
async def test_process_object_cover_click_sets_fsm_and_asks_photo() -> None:
    from platforms.blogger_flow import process_object_cover_click
    from platforms.telegram_states import BloggerFlowStates

    post_id = blogger_post_cache.remember(504, _SAMPLE)
    callback = MagicMock()
    callback.from_user.id = 504
    callback.data = f"{msg.CB_COVER_GENERATE_PREFIX}object:{post_id}"
    callback.message.chat.id = 4
    callback.message.message_id = 13
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    await process_object_cover_click(callback, state)

    state.update_data.assert_awaited_once_with(current_post_id=post_id)
    state.set_state.assert_awaited_once_with(BloggerFlowStates.waiting_for_product_photo)
    callback.message.answer.assert_awaited_once_with(msg.TXT_BLOGGER_COVER_UPLOAD_OBJECT_HINT)
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_product_photo_runs_generation() -> None:
    from platforms.blogger_flow import capture_product_photo

    post_id = blogger_post_cache.remember(505, _SAMPLE)
    message = MagicMock()
    message.from_user.id = 505
    message.chat.id = 505
    message.photo = [MagicMock(file_id="small"), MagicMock(file_id="AgACAgIAlarge")]
    message.answer = AsyncMock()
    message.bot.send_chat_action = AsyncMock()
    state = MagicMock()
    state.get_data = AsyncMock(return_value={"current_post_id": post_id})
    state.clear = AsyncMock()

    with patch(
        "platforms.blogger_flow.run_product_cover_generation",
        AsyncMock(),
    ) as mock_gen:
        await capture_product_photo(message, state)

    state.clear.assert_awaited_once()
    message.bot.send_chat_action.assert_awaited_once()
    message.answer.assert_not_awaited()
    mock_gen.assert_awaited_once()
    assert mock_gen.await_args.kwargs["photo_file_id"] == "AgACAgIAlarge"
    assert mock_gen.await_args.kwargs["post_id"] == post_id


@pytest.mark.asyncio
async def test_product_photo_input_fallback() -> None:
    from platforms.blogger_flow import product_photo_input_fallback

    message = MagicMock()
    message.answer = AsyncMock()
    await product_photo_input_fallback(message)
    message.answer.assert_awaited_once_with(msg.TXT_BLOGGER_COVER_PRODUCT_PHOTO_FALLBACK)
