"""Флаг «режим доработки» для VK: peer_id → ожидание i2i-промпта (TTL 15 мин).

VK не имеет reply_to_message.photo.file_id — после нажатия callback-кнопки
«✏️ Доработать» или текста-триггера следующий промпт подхватывает bytes
из ``photo_edit_session`` только при активном refine-pending.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

DEFAULT_VK_REFINE_TTL_SEC = 900.0
_MAX_PEERS = 4096

_pending: dict[int, tuple[int, float]] = {}  # peer_id → (user_id, expires_at)


def mark_vk_refine_pending(peer_id: int, user_id: int, *, ttl_sec: float = DEFAULT_VK_REFINE_TTL_SEC) -> None:
    if peer_id <= 0 or user_id <= 0:
        return
    _pending[peer_id] = (user_id, time.monotonic() + ttl_sec)
    _trim_if_needed()
    logger.info("vk refine pending peer_id=%s uid=%s ttl=%ss", peer_id, user_id, int(ttl_sec))


def peek_vk_refine_pending(peer_id: int) -> int | None:
    """Возвращает user_id, если peer в режиме доработки, иначе None."""
    row = _pending.get(peer_id)
    if row is None:
        return None
    user_id, expires_at = row
    if expires_at <= time.monotonic():
        _pending.pop(peer_id, None)
        return None
    return user_id


def clear_vk_refine_pending(peer_id: int) -> None:
    _pending.pop(peer_id, None)


def _trim_if_needed() -> None:
    if len(_pending) <= _MAX_PEERS:
        return
    now = time.monotonic()
    expired = [pid for pid, (_, exp) in _pending.items() if exp <= now]
    for pid in expired:
        _pending.pop(pid, None)
    if len(_pending) <= _MAX_PEERS:
        return
    ordered = sorted(_pending.items(), key=lambda item: item[1][1])
    for pid, _ in ordered[: len(_pending) - _MAX_PEERS]:
        _pending.pop(pid, None)


def reset_vk_refine_pending_for_tests() -> None:
    _pending.clear()
