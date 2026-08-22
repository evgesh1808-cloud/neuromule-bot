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
    user_prompt: str | None = None
    reference_file_id: str | None = None
    generation_seed: int | None = None


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
    user_prompt: str | None = None,
    reference_file_id: str | None = None,
    generation_seed: int | None = None,
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

    ref_id = (reference_file_id or "").strip() or None
    prompt = (user_prompt or "").strip() or None

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
        user_prompt=prompt,
        reference_file_id=ref_id,
        generation_seed=generation_seed,
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


@dataclass(frozen=True, slots=True)
class SessionResultReference:
    """Источник последнего сгенерированного результата (не исходного селфи)."""

    telegram_file_id: str | None
    media_url: str | None
    reference_image_bytes: bytes | None
    reference_mime: str = "image/jpeg"


def resolve_session_result_reference(sess: PhotoEditSession) -> SessionResultReference:
    tg_id = (sess.telegram_file_id or "").strip() or None
    url = (sess.media_url or "").strip() or None
    raw = sess.reference_image_bytes
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    elif isinstance(raw, bytearray):
        raw = bytes(raw)
    elif raw is not None and not isinstance(raw, bytes):
        raw = None
    if tg_id:
        return SessionResultReference(
            telegram_file_id=tg_id,
            media_url=url,
            reference_image_bytes=None,
            reference_mime=sess.reference_mime,
        )
    if url:
        return SessionResultReference(
            telegram_file_id=None,
            media_url=url,
            reference_image_bytes=None,
            reference_mime=sess.reference_mime,
        )
    return SessionResultReference(
        telegram_file_id=None,
        media_url=None,
        reference_image_bytes=raw,
        reference_mime=sess.reference_mime,
    )


def session_has_result_image(sess: PhotoEditSession | None) -> bool:
    if sess is None:
        return False
    ref = resolve_session_result_reference(sess)
    return bool(ref.telegram_file_id or ref.media_url or ref.reference_image_bytes)


def build_format_change_prompt(original_prompt: str, aspect_ratio: str) -> str:
    """i2i: сохранить кадр/ракурс, сменить только aspect ratio."""
    base = (original_prompt or "").strip()
    preserve = (
        "Using the attached image as the exact visual reference, preserve the same subject, "
        "pose, face angle, expression, clothing, lighting, background, and composition. "
        f"Change only the output aspect ratio to {aspect_ratio}. "
        "Do not recreate from scratch or change the camera angle."
    )
    if base:
        return f"{preserve} Original scene context: {base}"
    return preserve


def build_photo_refine_edit_prompt(user_intent_en: str) -> str:
    """i2i «Доработать текстом»: правки к уже сгенерированному кадру, не новая сцена."""
    intent = (user_intent_en or "").strip() or "subtle quality improvements"
    return (
        "Using the attached image as the exact visual reference, preserve the same subjects, "
        "faces, pose, clothing, lighting, background, and overall composition unless the edit "
        f"request explicitly asks to change them. Apply only these edits: {intent}. "
        "Do not regenerate from scratch, do not replace with an unrelated scene, "
        "and do not invent new people or objects unless requested. "
        "Photorealistic, sharp focus, consistent colors."
    )


def clear_photo_edit_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


def update_photo_edit_session_aspect_ratio(user_id: int, aspect_ratio: str) -> None:
    """Обновляет aspect_ratio активной edit-сессии (multi-turn refine)."""
    sess = get_photo_edit_session(user_id)
    if sess is None:
        return
    ar = normalize_photo_aspect_ratio(aspect_ratio)
    if sess.aspect_ratio == ar:
        return
    _sessions[user_id] = PhotoEditSession(
        user_id=sess.user_id,
        image_model_id=sess.image_model_id,
        image_model_label=sess.image_model_label,
        aspect_ratio=ar,
        expires_at=sess.expires_at,
        platform=sess.platform,
        telegram_file_id=sess.telegram_file_id,
        media_url=sess.media_url,
        reference_image_bytes=sess.reference_image_bytes,
        reference_mime=sess.reference_mime,
        message_id=sess.message_id,
        chat_id=sess.chat_id,
        user_prompt=sess.user_prompt,
        reference_file_id=sess.reference_file_id,
        generation_seed=sess.generation_seed,
    )


def reset_photo_edit_sessions_for_tests() -> None:
    _sessions.clear()


async def persist_photo_edit_session(
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
    user_prompt: str | None = None,
    reference_file_id: str | None = None,
    generation_seed: int | None = None,
    ttl_sec: float = DEFAULT_EDIT_SESSION_TTL_SEC,
) -> PhotoEditSession | None:
    """In-memory сессия + долговременный якорь в БД (Telegram)."""
    sess = save_photo_edit_session(
        user_id,
        image_model_id=image_model_id,
        image_model_label=image_model_label,
        aspect_ratio=aspect_ratio,
        telegram_file_id=telegram_file_id,
        media_url=media_url,
        reference_image_bytes=reference_image_bytes,
        reference_mime=reference_mime,
        message_id=message_id,
        chat_id=chat_id,
        platform=platform,
        user_prompt=user_prompt,
        reference_file_id=reference_file_id,
        generation_seed=generation_seed,
        ttl_sec=ttl_sec,
    )
    if sess is None or platform != "telegram":
        return sess
    from services.repository import save_last_generated_image

    await save_last_generated_image(
        user_id,
        telegram_file_id=sess.telegram_file_id,
        media_url=sess.media_url,
        image_model_id=sess.image_model_id,
        image_model_label=sess.image_model_label,
        aspect_ratio=sess.aspect_ratio,
        user_prompt=sess.user_prompt,
    )
    return sess


async def get_or_restore_photo_edit_session(
    user_id: int,
    *,
    peer_id: int | None = None,
) -> PhotoEditSession | None:
    """Активная in-memory сессия или восстановление из БД («вечный якорь»)."""
    sess = get_photo_edit_session(user_id, peer_id=peer_id)
    if sess is not None and session_has_result_image(sess):
        return sess

    from services.repository import get_last_generated_image

    persisted = await get_last_generated_image(user_id)
    if not persisted:
        return None

    tg_id = persisted.get("telegram_file_id")
    url = persisted.get("media_url")
    if not tg_id and not url:
        return None

    restored = save_photo_edit_session(
        user_id,
        image_model_id=str(persisted.get("image_model_id") or "").strip(),
        image_model_label=str(persisted.get("image_model_label") or "модель").strip(),
        aspect_ratio=str(persisted.get("aspect_ratio") or DEFAULT_PHOTO_ASPECT_RATIO),
        telegram_file_id=tg_id,
        media_url=url,
        chat_id=peer_id,
        platform="telegram",
        user_prompt=persisted.get("user_prompt"),
    )
    if restored is None:
        return None
    if peer_id is not None and restored.chat_id is not None and restored.chat_id != peer_id:
        return None
    return restored
