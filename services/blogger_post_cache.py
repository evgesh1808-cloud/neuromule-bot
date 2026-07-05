"""In-memory кэш черновиков постов режима «Блогер» для inline-кнопок конструктора.

Ключ — короткий ``post_id`` (8 hex-символов). Хранит сырой ответ модели и
распарсенные блоки (хэштеги, промпт обложки). После рестарта бота кэш обнуляется.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from services.blogger_post_parser import BloggerPostParsed, parse_blogger_post


@dataclass(frozen=True)
class BloggerPostDraft:
    post_id: str
    user_id: int
    raw_text: str
    parsed: BloggerPostParsed
    hashtags_applied: bool = False

    @property
    def hashtags(self) -> str | None:
        return self.parsed.hashtags

    @property
    def image_prompt(self) -> str | None:
        return self.parsed.image_prompt


_BY_ID: dict[str, BloggerPostDraft] = {}
_LAST_BY_USER: dict[int, str] = {}


def remember(user_id: int, raw_text: str) -> str:
    """Сохраняет черновик и возвращает ``post_id`` для callback_data."""
    post_id = secrets.token_hex(4)
    draft = BloggerPostDraft(
        post_id=post_id,
        user_id=int(user_id),
        raw_text=raw_text,
        parsed=parse_blogger_post(raw_text),
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


def mark_hashtags_applied(post_id: str, user_id: int) -> BloggerPostDraft | None:
    draft = get(post_id, user_id)
    if draft is None:
        return None
    updated = BloggerPostDraft(
        post_id=draft.post_id,
        user_id=draft.user_id,
        raw_text=draft.raw_text,
        parsed=draft.parsed,
        hashtags_applied=True,
    )
    _BY_ID[post_id] = updated
    return updated
