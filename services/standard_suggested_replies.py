"""Suggested Replies для роли ``standard``: парсинг ``===КНОПКИ===`` + callback."""

from __future__ import annotations

import logging
import re
import secrets
from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from content import messages as msg

logger = logging.getLogger(__name__)

BUTTONS_MARKER = "===КНОПКИ==="
_MAX_LABELS = 3
_MAX_LABEL_CHARS = 64
_CONTEXT_ID_LEN = 8
# Telegram Bot API: callback_data 1–64 bytes (UTF-8).
_TG_CALLBACK_DATA_MAX_BYTES = 64

# FREE: если модель забыла / обрезала ===КНОПКИ=== — железные подсказки.
# Короткие лейблы (≤20 символов) стабильно влезают в chat_hint: callback_data.
FREE_FALLBACK_SUGGESTED_REPLIES: tuple[str, ...] = (
    "Можно подробнее?",
    "Другой вариант?",
    "Как применить?",
)

# context_id -> (user_id, labels) — только fallback для длинных подписей / legacy
_CACHE: dict[str, tuple[int, tuple[str, ...]]] = {}
_BY_USER: dict[int, str] = {}


def split_suggested_replies(
    text: str,
    *,
    fallback_if_missing: bool = False,
) -> tuple[str, list[str]]:
    """Отделяет тело ответа от блока ``===КНОПКИ===`` (если есть).

    ``fallback_if_missing=True`` (FREE standard): при отсутствии маркера/лейблов
    и непустом теле ответа подставляет ``FREE_FALLBACK_SUGGESTED_REPLIES``.
    """
    raw = text or ""
    idx = raw.find(BUTTONS_MARKER)
    if idx < 0:
        # Модель иногда пишет маркер в другом регистре / с пробелами
        m = re.search(r"===?\s*КНОПКИ\s*===?", raw, flags=re.IGNORECASE)
        if not m:
            body = raw.strip()
            if fallback_if_missing and body:
                logger.info("suggested_replies: FREE fallback — marker missing")
                return body, list(FREE_FALLBACK_SUGGESTED_REPLIES)
            return body, []
        idx = m.start()
        marker_end = m.end()
    else:
        marker_end = idx + len(BUTTONS_MARKER)

    body = raw[:idx].rstrip()
    tail = raw[marker_end:]
    labels: list[str] = []
    for line in tail.splitlines():
        label = (line or "").strip()
        if not label:
            continue
        # Убираем маркеры списка / нумерацию
        label = re.sub(r"^[\d]+[.)]\s*", "", label)
        label = re.sub(r"^[-•*]\s*", "", label)
        label = label.strip()
        if not label:
            continue
        labels.append(label[:_MAX_LABEL_CHARS])
        if len(labels) >= _MAX_LABELS:
            break
    if not labels and fallback_if_missing and body.strip():
        logger.info("suggested_replies: FREE fallback — empty labels after marker")
        return body, list(FREE_FALLBACK_SUGGESTED_REPLIES)
    return body, labels


def remember_suggested_replies(user_id: int, labels: Sequence[str]) -> str | None:
    """Кладёт подписи в кэш; возвращает ``context_id`` или ``None`` если пусто.

    Нужен только как fallback, когда текст не помещается в ``chat_hint:``.
    """
    clean = tuple(str(x).strip()[:_MAX_LABEL_CHARS] for x in labels if str(x).strip())
    if not clean:
        return None
    context_id = secrets.token_hex(_CONTEXT_ID_LEN // 2)
    prev = _BY_USER.get(int(user_id))
    if prev and prev in _CACHE:
        _CACHE.pop(prev, None)
    _CACHE[context_id] = (int(user_id), clean[:_MAX_LABELS])
    _BY_USER[int(user_id)] = context_id
    return context_id


def callback_data_fits(data: str) -> bool:
    return len((data or "").encode("utf-8")) <= _TG_CALLBACK_DATA_MAX_BYTES


def build_chat_hint_callback(label: str) -> str | None:
    """``chat_hint:<текст>`` если укладывается в лимит Telegram, иначе ``None``."""
    text = (label or "").strip()
    if not text:
        return None
    data = f"{msg.CB_CHAT_HINT_PREFIX}{text}"
    if callback_data_fits(data):
        return data
    return None


def parse_chat_hint_callback(data: str) -> str | None:
    """``chat_hint:<текст>`` → текст вопроса."""
    prefix = msg.CB_CHAT_HINT_PREFIX
    raw = data or ""
    if not raw.startswith(prefix):
        return None
    text = raw[len(prefix) :].strip()
    return text or None


def resolve_suggested_reply(
    context_id: str,
    index: int,
    *,
    user_id: int,
) -> str | None:
    """Достаёт полный текст кнопки по legacy ``std_reply:<idx>:<context_id>``."""
    cid = (context_id or "").strip()
    entry = _CACHE.get(cid)
    if entry is None:
        return None
    owner_id, labels = entry
    if int(owner_id) != int(user_id):
        return None
    if index < 0 or index >= len(labels):
        return None
    return labels[index]


def resolve_suggested_reply_latest(user_id: int, index: int) -> str | None:
    """Мягкий fallback: последняя сессия подсказок пользователя по индексу."""
    cid = _BY_USER.get(int(user_id))
    if not cid:
        return None
    return resolve_suggested_reply(cid, index, user_id=user_id)


def parse_std_reply_callback(data: str) -> tuple[int, str] | None:
    """``std_reply:<index>:<context_id>`` → ``(index, context_id)``."""
    prefix = msg.CB_STD_REPLY_PREFIX
    raw = (data or "").strip()
    if not raw.startswith(prefix):
        return None
    rest = raw[len(prefix) :]
    if ":" not in rest:
        return None
    idx_s, context_id = rest.split(":", 1)
    context_id = context_id.strip()
    if not context_id:
        return None
    try:
        index = int(idx_s)
    except ValueError:
        return None
    if index < 0 or index >= _MAX_LABELS:
        return None
    return index, context_id


def build_suggested_replies_keyboard(
    context_id: str,
    labels: Sequence[str],
) -> InlineKeyboardMarkup | None:
    """Инлайн-кнопки Suggested Replies под ответом standard.

    Предпочтительно ``chat_hint:<текст>`` (без UUID-сессии). Legacy
    ``std_reply:<idx>:<context_id>`` — только если текст не влезает в 64 байта.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i, label in enumerate(labels):
        text = (label or "").strip()
        if not text:
            continue
        # Telegram button text limit ~64
        btn_text = text if len(text) <= 64 else text[:61] + "…"
        direct = build_chat_hint_callback(text)
        if direct is not None:
            callback_data = direct
        else:
            callback_data = f"{msg.CB_STD_REPLY_PREFIX}{i}:{context_id}"
            if not callback_data_fits(callback_data):
                logger.warning(
                    "suggested reply callback too long idx=%s len=%s",
                    i,
                    len(callback_data.encode("utf-8")),
                )
                continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=callback_data,
                )
            ]
        )
        if len(rows) >= _MAX_LABELS:
            break
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_standard_zero_balance_keyboard() -> InlineKeyboardMarkup:
    """Тариф / кристаллы / рефералка при нулевом балансе на Suggested Reply."""
    from platforms.telegram_utils import _invite_switch_query

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Повысить тариф до VIP",
                    callback_data=msg.CB_OPEN_TARIFFS,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Докупить кристаллы отдельно",
                    callback_data=msg.CB_BUY_CRYSTALS_ONLY_MENU,
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пригласить друзей",
                    switch_inline_query=_invite_switch_query(),
                )
            ],
        ]
    )


def clear_suggested_replies_for_tests() -> None:
    """Только тесты."""
    _CACHE.clear()
    _BY_USER.clear()
