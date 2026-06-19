"""In-memory кэш последнего музыкального трека пользователя для апсейлов.

Используется кнопкой ``Продлить трек (+1 мин)`` — мы помним стиль и
``clip_id`` последнего успешного Suno-рендера, чтобы перезапустить
генерацию с ``continue_clip_id``. Хранится только в памяти процесса —
после рестарта бота история обнуляется, и пользователь увидит мягкий
алерт «Сначала запиши любую композицию».
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LastMusicRequest:
    style: str
    lyrics: str | None
    make_instrumental: bool
    clip_id: str | None


_LAST_BY_USER: dict[int, LastMusicRequest] = {}


def remember(
    user_id: int,
    *,
    style: str,
    lyrics: str | None,
    make_instrumental: bool,
    clip_id: str | None,
) -> None:
    _LAST_BY_USER[int(user_id)] = LastMusicRequest(
        style=style,
        lyrics=lyrics,
        make_instrumental=bool(make_instrumental),
        clip_id=clip_id,
    )


def get(user_id: int) -> LastMusicRequest | None:
    return _LAST_BY_USER.get(int(user_id))


def clear(user_id: int) -> None:
    _LAST_BY_USER.pop(int(user_id), None)
