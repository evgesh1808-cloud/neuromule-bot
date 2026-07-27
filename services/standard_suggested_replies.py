"""Suggested Replies для роли ``standard``: парсинг ``===КНОПКИ===`` + callback."""

from __future__ import annotations

import html
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
_CHAT_HINT_PREFIX_BYTES = len(msg.CB_CHAT_HINT_PREFIX.encode("utf-8"))

# FREE: если модель забыла / обрезала ===КНОПКИ=== — железные подсказки.
# Короткие лейблы (≤20 символов) стабильно влезают в chat_hint: callback_data.
FREE_FALLBACK_SUGGESTED_REPLIES: tuple[str, ...] = (
    "Можно подробнее?",
    "Другой вариант?",
    "Как применить?",
)

# context_id -> (user_id, labels) — только legacy ``std_reply:`` (старые сообщения)
_CACHE: dict[str, tuple[int, tuple[str, ...]]] = {}
_BY_USER: dict[int, str] = {}


def sanitize_suggested_label(label: str) -> str:
    """Убирает HTML/кавычки/мусор — иначе callback ломается или выглядит «мёртвым»."""
    text = html.unescape((label or "").strip())
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) >= 2 and text[0] in "\"'«“„" and text[-1] in "\"'»”":
        text = text[1:-1].strip()
    return text[:_MAX_LABEL_CHARS]


def fit_label_for_chat_hint(label: str) -> str:
    """Обрезает лейбл так, чтобы ``chat_hint:<текст>`` всегда ≤64 байт UTF-8."""
    text = sanitize_suggested_label(label)
    if not text:
        return ""
    budget = _TG_CALLBACK_DATA_MAX_BYTES - _CHAT_HINT_PREFIX_BYTES
    if budget <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text
    ell = "…"
    ell_b = ell.encode("utf-8")
    keep = max(0, budget - len(ell_b))
    cut = raw[:keep].decode("utf-8", errors="ignore").rstrip()
    if not cut:
        return ""
    return cut + ell


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
        label = sanitize_suggested_label(line or "")
        if not label:
            continue
        # Убираем маркеры списка / нумерацию
        label = re.sub(r"^[\d]+[.)]\s*", "", label)
        label = re.sub(r"^[-•*]\s*", "", label)
        label = sanitize_suggested_label(label)
        if not label:
            continue
        # Сразу под лимит callback — иначе раньше уходили в мёртвый std_reply UUID.
        labels.append(fit_label_for_chat_hint(label))
        if len(labels) >= _MAX_LABELS:
            break
    labels = [x for x in labels if x]
    if not labels and fallback_if_missing and body.strip():
        logger.info("suggested_replies: FREE fallback — empty labels after marker")
        return body, list(FREE_FALLBACK_SUGGESTED_REPLIES)
    return body, labels


def remember_suggested_replies(user_id: int, labels: Sequence[str]) -> str | None:
    """Кладёт подписи в кэш; возвращает ``context_id`` или ``None`` если пусто.

    Нужен только для legacy ``std_reply:`` (старые сообщения в чате).
    """
    clean = tuple(
        fit_label_for_chat_hint(str(x)) for x in labels if fit_label_for_chat_hint(str(x))
    )
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
    """``chat_hint:<текст>`` — всегда усечённый под 64 байта (не ``None`` из‑за длины)."""
    text = fit_label_for_chat_hint(label)
    if not text:
        return None
    data = f"{msg.CB_CHAT_HINT_PREFIX}{text}"
    if callback_data_fits(data):
        return data
    logger.warning("suggested_replies: chat_hint still too long after fit len=%s", len(data.encode()))
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

    Всегда ``chat_hint:<текст>`` (текст усечён под 64 байта). Legacy
    ``std_reply:`` больше не создаём — in-memory UUID ломал FREE после рестарта.
    ``context_id`` оставлен в сигнатуре для совместимости вызовов.
    """
    _ = context_id  # legacy API
    rows: list[list[InlineKeyboardButton]] = []
    for label in labels:
        callback_data = build_chat_hint_callback(label)
        if callback_data is None:
            continue
        text = parse_chat_hint_callback(callback_data) or ""
        if not text:
            continue
        # Telegram button text limit ~64 символа
        btn_text = text if len(text) <= 64 else text[:61] + "…"
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
