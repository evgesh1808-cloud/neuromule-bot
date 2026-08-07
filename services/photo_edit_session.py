"""
In-memory сессия multi-turn i2i после успешной генерации (TTL 15 мин).

Хранит file_id / URL / bytes последнего результата + model/aspect для
кнопки «✏️ Доработать» и reply-to-photo.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

from services.photo_aspect_ratio import DEFAULT_PHOTO_ASPECT_RATIO, normalize_photo_aspect_ratio

logger = logging.getLogger(__name__)

DEFAULT_EDIT_SESSION_TTL_SEC = 900.0
_MAX_SESSIONS = 4096

PlatformKind = Literal["telegram", "vk"]

_sessions: dict[int, "PhotoEditSession"] = {}


@dataclass(frozen=True, slots=True)
class PhotoEditSession:
    user_id: int
    image_model_id: str
    image_model_label: str
    aspect_ratio: str
    expires_at: float
    platform: PlatformKind = "telegram"
    telegram_file_id: str | None = None
    media_url: str | None = None
    reference_image_bytes: bytes | None = None
    reference_mime: str = "image/jpeg"
    message_id: int | None = None
    chat_id: int | None = None


def _evict_expired(now: float | None = None) -> None:
    ts = time.monotonic() if now is None else now
    expired = [uid for uid, sess in _sessions.items() if sess.expires_at <= ts]
    for uid in expired:
        _sessions.pop(uid, None)


def _trim_if_needed() -> None:
    if len(_sessions) <= _MAX_SESSIONS:
        return
    _evict_expired()
    if len(_sessions) <= _MAX_SESSIONS:
        return
    ordered = sorted(_sessions.items(), key=lambda item: item[1].expires_at)
    for uid, _ in ordered[: len(_sessions) - _MAX_SESSIONS]:
        _sessions.pop(uid, None)


def save_photo_edit_session(
    user_id: int,
    *,
    image_model_id: str,
    image_model_label: str,
    aspect_ratio: str | None = None,
    telegram_file_id: str | None = None,
    media_url: str | None = None,
    reference_image_bytes: bytes | None = None,
    reference_mime: str = "image/jpeg",
    message_id: int | None = None,
    chat_id: int | None = None,
    platform: PlatformKind = "telegram",
    ttl_sec: float = DEFAULT_EDIT_SESSION_TTL_SEC,
) -> PhotoEditSession | None:
    """Сохраняет контекст последней генерации; нужен хотя бы один источник изображения."""
    if user_id <= 0:
        return None

    tg_id = (telegram_file_id or "").strip() or None
    url = (media_url or "").strip() or None
    raw = reference_image_bytes
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    elif isinstance(raw, bytearray):
        raw = bytes(raw)
    elif raw is not None and not isinstance(raw, bytes):
        raise TypeError("reference_image_bytes must be bytes")

    if not tg_id and not url and not raw:
        return None

    sess = PhotoEditSession(
        user_id=user_id,
        image_model_id=(image_model_id or "").strip(),
        image_model_label=(image_model_label or "модель").strip(),
        aspect_ratio=normalize_photo_aspect_ratio(aspect_ratio),
        expires_at=time.monotonic() + ttl_sec,
        platform=platform,
        telegram_file_id=tg_id,
        media_url=url,
        reference_image_bytes=raw,
        reference_mime=(reference_mime or "image/jpeg").strip() or "image/jpeg",
        message_id=message_id,
        chat_id=chat_id,
    )
    _sessions[user_id] = sess
    _trim_if_needed()
    logger.info(
        "photo edit session saved uid=%s platform=%s msg_id=%s ttl=%ss",
        user_id,
        platform,
        message_id,
        int(ttl_sec),
    )
    return sess


def get_photo_edit_session(user_id: int, *, peer_id: int | None = None) -> PhotoEditSession | None:
    sess = _sessions.get(user_id)
    if sess is None:
        return None
    if sess.expires_at <= time.monotonic():
        _sessions.pop(user_id, None)
        return None
    if peer_id is not None and sess.chat_id is not None and sess.chat_id != peer_id:
        return None
    return sess


def clear_photo_edit_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


def reset_photo_edit_sessions_for_tests() -> None:
    _sessions.clear()
