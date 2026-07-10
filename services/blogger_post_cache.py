"""In-memory кэш черновиков постов режима «Блогер» для inline-кнопок конструктора.

Ключ — короткий ``post_id`` (8 hex-символов). Хранит полный сырой ответ модели
(все ``===``-секции) независимо от того, что показано в ``editMessageText``.
Привязка ``(chat_id, message_id) → post_id`` сохраняется после кликов по кнопкам.
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
    chat_id: int | None = None
    message_id: int | None = None
    display_text: str | None = None

    @property
    def hashtags(self) -> str | None:
        return self.parsed.hashtags

    @property
    def image_prompt(self) -> str | None:
        return self.parsed.image_prompt


_BY_ID: dict[str, BloggerPostDraft] = {}
_LAST_BY_USER: dict[int, str] = {}
_BY_MESSAGE: dict[tuple[int, int], str] = {}


def _store_draft(draft: BloggerPostDraft) -> BloggerPostDraft:
    _BY_ID[draft.post_id] = draft
    _LAST_BY_USER[int(draft.user_id)] = draft.post_id
    if draft.chat_id is not None and draft.message_id is not None:
        _BY_MESSAGE[(int(draft.chat_id), int(draft.message_id))] = draft.post_id
    return draft


def remember(user_id: int, raw_text: str) -> str:
    """Сохраняет черновик и возвращает ``post_id`` для callback_data."""
    post_id = secrets.token_hex(4)
    draft = BloggerPostDraft(
        post_id=post_id,
        user_id=int(user_id),
        raw_text=raw_text,
        parsed=parse_blogger_post(raw_text),
    )
    _store_draft(draft)
    return post_id


def get(post_id: str, user_id: int) -> BloggerPostDraft | None:
    draft = _BY_ID.get(str(post_id))
    if draft is None or draft.user_id != int(user_id):
        return None
    return draft


def get_by_message(chat_id: int, message_id: int, user_id: int) -> BloggerPostDraft | None:
    """Резервный lookup: сообщение в чате → тот же ``post_id`` после editMessageText."""
    post_id = _BY_MESSAGE.get((int(chat_id), int(message_id)))
    if not post_id:
        return None
    return get(post_id, user_id)


def get_last(user_id: int) -> BloggerPostDraft | None:
    post_id = _LAST_BY_USER.get(int(user_id))
    if not post_id:
        return None
    return _BY_ID.get(post_id)


def bind_telegram_message(
    post_id: str,
    user_id: int,
    *,
    chat_id: int,
    message_id: int,
) -> BloggerPostDraft | None:
    """Фиксирует связь inline-кнопок с Telegram-сообщением (не меняя ``post_id``)."""
    draft = get(post_id, user_id)
    if draft is None:
        return None
    updated = BloggerPostDraft(
        post_id=draft.post_id,
        user_id=draft.user_id,
        raw_text=draft.raw_text,
        parsed=draft.parsed,
        hashtags_applied=draft.hashtags_applied,
        chat_id=int(chat_id),
        message_id=int(message_id),
        display_text=draft.display_text,
    )
    return _store_draft(updated)


def mark_hashtags_applied(
    post_id: str,
    user_id: int,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
    display_text: str | None = None,
) -> BloggerPostDraft | None:
    """Помечает хэштеги добавленными; ``raw_text`` и секции для adapt/art не трогаем."""
    draft = get(post_id, user_id)
    if draft is None:
        return None
    updated = BloggerPostDraft(
        post_id=draft.post_id,
        user_id=draft.user_id,
        raw_text=draft.raw_text,
        parsed=draft.parsed,
        hashtags_applied=True,
        chat_id=chat_id if chat_id is not None else draft.chat_id,
        message_id=message_id if message_id is not None else draft.message_id,
        display_text=display_text if display_text is not None else draft.display_text,
    )
    return _store_draft(updated)
