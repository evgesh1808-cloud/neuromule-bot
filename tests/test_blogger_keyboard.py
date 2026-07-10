"""Тесты парсера, клавиатуры и кэша режима «Блогер»."""

from __future__ import annotations

from content import messages as msg
from content.inline_keyboards import get_blogger_adapt_keyboard, get_blogger_keyboard
from services import blogger_post_cache
from services.blogger_post_parser import parse_blogger_post

import pytest


@pytest.fixture(autouse=True)
def _clear_blogger_post_cache() -> None:
    blogger_post_cache._BY_ID.clear()
    blogger_post_cache._LAST_BY_USER.clear()
    blogger_post_cache._BY_MESSAGE.clear()
    yield
    blogger_post_cache._BY_ID.clear()
    blogger_post_cache._LAST_BY_USER.clear()
    blogger_post_cache._BY_MESSAGE.clear()

_SAMPLE = """===ХУКИ===
[Вариант 1 (Интрига)]: Заголовок

===ТЕЛО ПОСТА===
Текст с <b>инсайтом</b>.

===ПРИЗЫВЫ К ДЕЙСТВИЮ===
[Вариант А (Вовлечение)]: Вопрос?

===ХЭШТЕГИ===
[Тематические]: #AI #Tech
[Навигационные]: #Блог_инсайт

===ПРОМПТ ДЛЯ КАРТИНКИ===
A professional cinematic photo of sunset, 4k --ar 16:9
"""


def test_parse_blogger_post_sections() -> None:
    parsed = parse_blogger_post(_SAMPLE)
    assert parsed.hashtags is not None
    assert "#AI" in parsed.hashtags
    assert parsed.image_prompt is not None
    assert "sunset" in parsed.image_prompt
    assert parsed.body is not None
    assert "инсайтом" in parsed.body
    assert "===ХЭШТЕГИ===" not in parsed.display_plain()
    assert "===ПРОМПТ" not in parsed.display_plain()
    assert "инсайтом" in parsed.display_plain()


def test_get_blogger_keyboard_three_rows() -> None:
    kb = get_blogger_keyboard("a1b2c3d4")
    assert len(kb.inline_keyboard) == 3
    hash_btn, adapt_btn, art_btn = (row[0] for row in kb.inline_keyboard)
    assert hash_btn.text == "#️⃣ Подобрать хэштеги"
    assert hash_btn.callback_data == f"{msg.CB_BLOG_HASH_PREFIX}a1b2c3d4"
    assert adapt_btn.callback_data == f"{msg.CB_BLOG_ADAPT_PREFIX}a1b2c3d4"
    assert art_btn.callback_data == f"{msg.CB_BLOGGER_COVER_PREFIX}a1b2c3d4"


def test_get_blogger_keyboard_without_hashtags() -> None:
    kb = get_blogger_keyboard("a1b2c3d4", include_hashtags=False)
    assert len(kb.inline_keyboard) == 2
    assert kb.inline_keyboard[0][0].text.startswith("🔄")


def test_get_blogger_adapt_keyboard() -> None:
    kb = get_blogger_adapt_keyboard("a1b2c3d4")
    assert len(kb.inline_keyboard) == 5
    video_btn, vc_btn, vk_btn, tg_btn = (row[0] for row in kb.inline_keyboard[:4])
    assert video_btn.text == msg.BTN_BLOGGER_ADAPT_VIDEO
    assert video_btn.callback_data == msg.CB_ADAPT_TARGET_VIDEO
    assert vc_btn.callback_data == msg.CB_ADAPT_TARGET_VC
    assert vk_btn.callback_data == msg.CB_ADAPT_TARGET_VK
    assert tg_btn.callback_data == msg.CB_ADAPT_TARGET_TG_MAX
    back_btn = kb.inline_keyboard[-1][0]
    assert back_btn.callback_data == f"{msg.CB_BLOG_BACK_PREFIX}a1b2c3d4"


def test_blogger_post_cache_remember_and_get() -> None:
    post_id = blogger_post_cache.remember(9001, _SAMPLE)
    draft = blogger_post_cache.get(post_id, 9001)
    assert draft is not None
    assert draft.hashtags is not None
    assert draft.image_prompt is not None
    assert blogger_post_cache.get(post_id, 9002) is None
    updated = blogger_post_cache.mark_hashtags_applied(post_id, 9001)
    assert updated is not None
    assert updated.hashtags_applied is True


def test_blogger_post_cache_survives_hashtags_edit_message() -> None:
    post_id = blogger_post_cache.remember(9001, _SAMPLE)
    chat_id, message_id = 42, 1001
    display = "Пост в чате\n\n#AI #Tech"

    blogger_post_cache.bind_telegram_message(
        post_id,
        9001,
        chat_id=chat_id,
        message_id=message_id,
    )
    blogger_post_cache.mark_hashtags_applied(
        post_id,
        9001,
        chat_id=chat_id,
        message_id=message_id,
        display_text=display,
    )

    by_message = blogger_post_cache.get_by_message(chat_id, message_id, 9001)
    assert by_message is not None
    assert by_message.post_id == post_id
    assert by_message.hashtags_applied is True
    assert by_message.display_text == display
    assert by_message.image_prompt is not None
    assert by_message.parsed.body is not None
