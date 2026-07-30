"""Callback «Скачать без сжатия»: короткий ключ → Telegram file_id из кэша.

Telegram ограничивает ``callback_data`` 64 байтами. ``file_id`` часто длиннее,
поэтому в кнопку кладём типизированный payload:

* ``dl_file:f:<file_id>`` — прямой id, если влезает в 64 байта;
* ``dl_file:t:<task_id>`` — резолв через ``last_share_media``;
* ``dl_file:k:<token>`` — ephemeral-токен (TTL 48ч) с привязкой к user_id.

Сервер не скачивает байты повторно: ``send_document(document=file_id)``.
"""

from __future__ import annotations

import secrets
import time
from typing import Final

from content import messages as msg
from services import last_share_media

CB_DL_FILE_PREFIX: Final[str] = msg.CB_DL_FILE_PREFIX
_MAX_CALLBACK_BYTES: Final[int] = 64
_TOKEN_TTL_SEC: Final[float] = 48 * 60 * 60

# token -> (file_id, user_id, monotonic_ts)
_TOKEN_CACHE: dict[str, tuple[str, int, float]] = {}


def _fits(callback: str) -> bool:
    return len(callback.encode("utf-8")) <= _MAX_CALLBACK_BYTES


def remember_dl_token(file_id: str, *, user_id: int) -> str:
    """Короткий токен, если даже task_id не влезает в 64 байта."""
    fid = (file_id or "").strip()
    if not fid:
        raise ValueError("file_id is empty")
    token = secrets.token_urlsafe(6)
    _TOKEN_CACHE[token] = (fid, int(user_id), time.monotonic())
    return token


def build_dl_file_callback(
    *,
    file_id: str = "",
    task_id: str = "",
    user_id: int = 0,
) -> str:
    """Собрать ``callback_data`` для кнопки скачивания."""
    fid = (file_id or "").strip()
    if fid:
        direct = f"{CB_DL_FILE_PREFIX}f:{fid}"
        if _fits(direct):
            return direct

    tid = (task_id or "").strip()
    if tid:
        via_task = f"{CB_DL_FILE_PREFIX}t:{tid}"
        if _fits(via_task):
            return via_task

    if not fid:
        raise ValueError("need file_id or task_id for download callback")
    token = remember_dl_token(fid, user_id=user_id)
    return f"{CB_DL_FILE_PREFIX}k:{token}"


def resolve_dl_file_id(payload: str, *, user_id: int) -> str | None:
    """Достать Telegram ``file_id`` из payload кнопки (после префикса ``dl_file:``)."""
    raw = (payload or "").strip()
    if not raw:
        return None
    uid = int(user_id)

    kind, _, rest = raw.partition(":")
    if not rest:
        # Legacy / прямой file_id без типизации (если когда-то зашили целиком).
        kind, rest = "f", raw

    if kind == "f":
        return rest.strip() or None

    if kind == "t":
        entry = last_share_media.get_by_task(rest.strip())
        if entry is None or int(entry.user_id) != uid:
            return None
        return (entry.file_id or "").strip() or None

    if kind == "k":
        cached = _TOKEN_CACHE.get(rest.strip())
        if cached is None:
            return None
        fid, owner, ts = cached
        if time.monotonic() - ts > _TOKEN_TTL_SEC:
            _TOKEN_CACHE.pop(rest.strip(), None)
            return None
        if int(owner) != uid:
            return None
        return fid

    return None


def reset_dl_tokens_for_tests() -> None:
    _TOKEN_CACHE.clear()
