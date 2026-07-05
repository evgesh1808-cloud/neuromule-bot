"""In-memory кэш черновиков постов режима «Блогер» для inline-кнопок конструктора.

Ключ — короткий ``post_id`` (8 hex-символов), значение — сырой ответ модели
с разделителями ``===``. После рестарта бота кэш обнуляется.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class BloggerPostDraft:
    post_id: str
    user_id: int
    raw_text: str


_BY_ID: dict[str, BloggerPostDraft] = {}
_LAST_BY_USER: dict[int, str] = {}


def remember(user_id: int, raw_text: str) -> str:
    """Сохраняет черновик и возвращает ``post_id`` для callback_data."""
    post_id = secrets.token_hex(4)
    draft = BloggerPostDraft(
        post_id=post_id,
        user_id=int(user_id),
        raw_text=raw_text,
    )
    _BY_ID[post_id] = draft
    _LAST_BY_USER[int(user_id)] = post_id
    return post_id


def get(post_id: str, user_id: int) -> BloggerPostDraft | None:
    draft = _BY_ID.get(str(post_id))
    if draft is None or draft.user_id != int(user_id):
        return None
    return draft


def get_last(user_id: int) -> BloggerPostDraft | None:
    post_id = _LAST_BY_USER.get(int(user_id))
    if not post_id:
        return None
    return _BY_ID.get(post_id)
