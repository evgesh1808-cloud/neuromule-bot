"""Тесты клавиатуры и кэша конструктора режима «Блогер»."""

from __future__ import annotations

from content import messages as msg
from content.inline_keyboards import get_blogger_keyboard
from services import blogger_post_cache


def test_get_blogger_keyboard_three_rows() -> None:
    kb = get_blogger_keyboard("a1b2c3d4")
    assert len(kb.inline_keyboard) == 3
    hash_btn, adapt_btn, art_btn = (row[0] for row in kb.inline_keyboard)
    assert hash_btn.text == "#️⃣ Подобрать хэштеги"
    assert hash_btn.callback_data == f"{msg.CB_BLOG_HASH_PREFIX}a1b2c3d4"
    assert adapt_btn.callback_data == f"{msg.CB_BLOG_ADAPT_PREFIX}a1b2c3d4"
    assert art_btn.callback_data == f"{msg.CB_BLOG_ART_PREFIX}a1b2c3d4"


def test_blogger_post_cache_remember_and_get() -> None:
    post_id = blogger_post_cache.remember(9001, "===ХУКИ===\ntest")
    draft = blogger_post_cache.get(post_id, 9001)
    assert draft is not None
    assert draft.raw_text.startswith("===ХУКИ===")
    assert blogger_post_cache.get(post_id, 9002) is None
    last = blogger_post_cache.get_last(9001)
    assert last is not None
    assert last.post_id == post_id
