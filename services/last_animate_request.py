"""In-memory кеш последнего запроса на оживление фото (исходник + промпт).

Нужен для кнопки «🔄 Перегенерировать видео» под готовым mp4.
При рестарте бота кеш очищается — штатно для in-memory side-effect.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LastAnimateRequest:
    source_file_id: str
    motion_prompt: str | None = None


_LAST_BY_USER: dict[int, LastAnimateRequest] = {}


def remember(
    user_id: int,
    *,
    source_file_id: str,
    motion_prompt: str | None = None,
) -> None:
    fid = (source_file_id or "").strip()
    if not fid:
        return
    prompt = (motion_prompt or "").strip() or None
    _LAST_BY_USER[int(user_id)] = LastAnimateRequest(
        source_file_id=fid,
        motion_prompt=prompt,
    )


def get(user_id: int) -> LastAnimateRequest | None:
    return _LAST_BY_USER.get(int(user_id))


def clear(user_id: int) -> None:
    _LAST_BY_USER.pop(int(user_id), None)
