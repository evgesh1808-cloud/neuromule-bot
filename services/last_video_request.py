"""In-memory кеш последнего видео-запроса пользователя.

Хранится в процессе бота. При рестарте кеш очищается — это сознательное
дизайн-решение: кнопки «🔁 Сгенерировать заново» / «🔍 Upscale» имеют смысл
только пока пользователь активно работает с конкретным результатом.

API:
    `remember(user_id, scenario_id, prompt, file_id)`
    `get(user_id) -> LastVideoRequest | None`
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LastVideoRequest:
    scenario_id: str
    prompt: str
    file_id: str | None


_LAST_BY_USER: dict[int, LastVideoRequest] = {}


def remember(
    user_id: int,
    *,
    scenario_id: str,
    prompt: str = "",
    file_id: str | None = None,
) -> None:
    sid = (scenario_id or "").strip()
    if not sid:
        return
    _LAST_BY_USER[int(user_id)] = LastVideoRequest(
        scenario_id=sid,
        prompt=(prompt or "").strip(),
        file_id=(file_id or "").strip() or None,
    )


def get(user_id: int) -> LastVideoRequest | None:
    return _LAST_BY_USER.get(int(user_id))


def clear(user_id: int) -> None:
    _LAST_BY_USER.pop(int(user_id), None)
