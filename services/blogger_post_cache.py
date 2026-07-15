"""Кэш черновиков постов режима «Блогер» для inline-кнопок конструктора.

In-memory слой + SQLite (``blogger_post_drafts``), чтобы кнопки работали после
рестарта процесса и не зависели только от привязки ``(chat_id, message_id)``.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from services.blogger_post_parser import (
    BloggerPostParsed,
    canonicalize_blogger_cache_raw,
    parse_blogger_post,
)

logger = logging.getLogger(__name__)

_BY_ID: dict[str, BloggerPostDraft] = {}
_LAST_BY_USER: dict[int, str] = {}
_BY_MESSAGE: dict[tuple[int, int], str] = {}


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


def _draft_from_row(row: dict[str, object]) -> BloggerPostDraft:
    raw_text = canonicalize_blogger_cache_raw(str(row.get("raw_text") or ""))
    return BloggerPostDraft(
        post_id=str(row["post_id"]),
        user_id=int(row["user_id"]),
        raw_text=raw_text,
        parsed=parse_blogger_post(raw_text),
        hashtags_applied=bool(row.get("hashtags_applied")),
        chat_id=row.get("chat_id"),  # type: ignore[arg-type]
        message_id=row.get("message_id"),  # type: ignore[arg-type]
        display_text=str(row["display_text"]) if row.get("display_text") else None,
    )


def _store_draft(draft: BloggerPostDraft) -> BloggerPostDraft:
    _BY_ID[draft.post_id] = draft
    _LAST_BY_USER[int(draft.user_id)] = draft.post_id
    if draft.chat_id is not None and draft.message_id is not None:
        _BY_MESSAGE[(int(draft.chat_id), int(draft.message_id))] = draft.post_id
    return draft


async def _persist_draft(draft: BloggerPostDraft) -> None:
    from services.repository import save_blogger_post_draft

    try:
        await save_blogger_post_draft(
            post_id=draft.post_id,
            user_id=draft.user_id,
            raw_text=draft.raw_text,
            hashtags_applied=draft.hashtags_applied,
            chat_id=draft.chat_id,
            message_id=draft.message_id,
            display_text=draft.display_text,
        )
    except Exception:
        logger.exception(
            "blogger_post_cache persist failed uid=%s post_id=%s",
            draft.user_id,
            draft.post_id,
        )


async def _hydrate_from_db(post_id: str, user_id: int) -> BloggerPostDraft | None:
    from services.repository import load_blogger_post_draft

    row = await load_blogger_post_draft(post_id, user_id)
    if row is None:
        return None
    draft = _draft_from_row(row)
    return _store_draft(draft)


async def _hydrate_last_from_db(user_id: int) -> BloggerPostDraft | None:
    from services.repository import load_last_blogger_post_draft

    row = await load_last_blogger_post_draft(user_id)
    if row is None:
        return None
    draft = _draft_from_row(row)
    return _store_draft(draft)


def remember(user_id: int, raw_text: str) -> str:
    """Синхронный in-memory save (legacy). Предпочтительно ``remember_async``."""
    canonical = canonicalize_blogger_cache_raw(raw_text)
    post_id = secrets.token_hex(4)
    draft = BloggerPostDraft(
        post_id=post_id,
        user_id=int(user_id),
        raw_text=canonical,
        parsed=parse_blogger_post(canonical),
    )
    _store_draft(draft)
    return post_id


async def ensure_canonical(draft: BloggerPostDraft) -> BloggerPostDraft:
    """Поднимает display-HTML черновик до канонического ``===``-формата при клике."""
    canonical = canonicalize_blogger_cache_raw(draft.raw_text)
    if canonical == draft.raw_text:
        return draft
    updated = BloggerPostDraft(
        post_id=draft.post_id,
        user_id=draft.user_id,
        raw_text=canonical,
        parsed=parse_blogger_post(canonical),
        hashtags_applied=draft.hashtags_applied,
        chat_id=draft.chat_id,
        message_id=draft.message_id,
        display_text=draft.display_text,
    )
    stored = _store_draft(updated)
    await _persist_draft(stored)
    return stored


async def remember_async(user_id: int, raw_text: str) -> str:
    """Сохраняет черновик в память и SQLite, возвращает ``post_id``."""
    post_id = remember(user_id, raw_text)
    draft = _BY_ID[post_id]
    await _persist_draft(draft)
    return post_id


def get(post_id: str, user_id: int) -> BloggerPostDraft | None:
    draft = _BY_ID.get(str(post_id))
    if draft is None or draft.user_id != int(user_id):
        return None
    return draft


async def resolve(post_id: str, user_id: int) -> BloggerPostDraft | None:
    """In-memory → SQLite fallback для callback-кнопок конструктора."""
    draft = get(post_id, user_id)
    if draft is None:
        draft = await _hydrate_from_db(post_id, user_id)
    if draft is None:
        return None
    return await ensure_canonical(draft)


async def resolve_by_message(chat_id: int, message_id: int, user_id: int) -> BloggerPostDraft | None:
    post_id = _BY_MESSAGE.get((int(chat_id), int(message_id)))
    if post_id:
        draft = await resolve(post_id, user_id)
        if draft is not None:
            return draft
    return await resolve_last(user_id)


async def resolve_last(user_id: int) -> BloggerPostDraft | None:
    post_id = _LAST_BY_USER.get(int(user_id))
    if post_id:
        draft = await resolve(post_id, user_id)
        if draft is not None:
            return draft
    return await _hydrate_last_from_db(user_id)


def get_by_message(chat_id: int, message_id: int, user_id: int) -> BloggerPostDraft | None:
    post_id = _BY_MESSAGE.get((int(chat_id), int(message_id)))
    if not post_id:
        return None
    return get(post_id, user_id)


def get_last(user_id: int) -> BloggerPostDraft | None:
    post_id = _LAST_BY_USER.get(int(user_id))
    if not post_id:
        return None
    return _BY_ID.get(post_id)


async def bind_telegram_message(
    post_id: str,
    user_id: int,
    *,
    chat_id: int,
    message_id: int,
) -> BloggerPostDraft | None:
    """Фиксирует связь inline-кнопок с Telegram-сообщением (не меняя ``post_id``)."""
    draft = await resolve(post_id, user_id)
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
    stored = _store_draft(updated)
    await _persist_draft(stored)
    return stored


async def mark_hashtags_applied(
    post_id: str,
    user_id: int,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
    display_text: str | None = None,
) -> BloggerPostDraft | None:
    """Помечает хэштеги добавленными; ``raw_text`` и секции для adapt/art не трогаем."""
    draft = await resolve(post_id, user_id)
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
    stored = _store_draft(updated)
    await _persist_draft(stored)
    return stored
