"""In-memory кэш «последняя сгенерированная медиа-карточка для шеринга».

Хранит результат успешного task'а (фото / видео / музыка / оживление) ровно
столько, сколько нужно пользователю для клика по кнопкам
``📢 Поделиться в Галерее`` и ``🚀 Переслать другу в ЛС``.

Намеренно in-memory — после рестарта бота кэш чист. Пользователь увидит
«⚠️ Не нашёл медиа для публикации» и сделает новую генерацию. Это
оптимальный компромисс: никаких хвостов в SQLite, нет утечек приватных
file_id, кросс-постинг работает только когда юзер «горячий».

Каждая карточка хранит:

* ``task_id`` — уникальный id задачи (источник для switch_inline_query).
* ``task_type`` — ``photo`` / ``video`` / ``animate`` / ``music``.
* ``media_url`` или ``file_id`` (одно из двух) — то, что отправили юзеру.
* ``prompt`` — текст промпта / стиля для подписи.
* ``user_id`` — автор.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

# Жизнь записи в кэше — 48 часов. После этого считаем шеринг устаревшим:
# юзер вряд ли пойдёт делиться вчерашним мемом, а file_id Telegram могут
# инвалидироваться за это время.
SHARE_CACHE_TTL_SEC: float = 48 * 60 * 60
# Период фонового GC (24 часа): достаточно редкий, чтобы не нагружать
# event-loop, и достаточно частый, чтобы кэш не раздувался.
GC_INTERVAL_SEC: float = 24 * 60 * 60


MediaTaskType = Literal["photo", "video", "animate", "music"]


@dataclass(frozen=True)
class ShareMediaEntry:
    user_id: int
    task_id: str
    task_type: MediaTaskType
    prompt: str
    media_url: str | None = None
    file_id: str | None = None


_BY_USER: dict[int, ShareMediaEntry] = {}
_BY_TASK: dict[str, ShareMediaEntry] = {}
# Параллельный словарь timestamp'ов для GC. Не помещаем поле в dataclass,
# чтобы тесты не зависели от него.
_TS: dict[str, float] = {}


def remember(
    *,
    user_id: int,
    task_id: str,
    task_type: MediaTaskType,
    prompt: str,
    media_url: str | None = None,
    file_id: str | None = None,
) -> ShareMediaEntry:
    if not media_url and not file_id:
        raise ValueError("ShareMediaEntry requires media_url OR file_id")
    entry = ShareMediaEntry(
        user_id=int(user_id),
        task_id=str(task_id),
        task_type=task_type,
        prompt=(prompt or "").strip(),
        media_url=media_url,
        file_id=file_id,
    )
    _BY_USER[entry.user_id] = entry
    _BY_TASK[entry.task_id] = entry
    _TS[entry.task_id] = time.monotonic()
    return entry


def get_by_user(user_id: int) -> ShareMediaEntry | None:
    return _BY_USER.get(int(user_id))


def get_by_task(task_id: str) -> ShareMediaEntry | None:
    return _BY_TASK.get(str(task_id))


def clear(user_id: int) -> None:
    entry = _BY_USER.pop(int(user_id), None)
    if entry is not None:
        _BY_TASK.pop(entry.task_id, None)
        _TS.pop(entry.task_id, None)


def purge_expired(*, ttl_sec: float = SHARE_CACHE_TTL_SEC) -> int:
    """Чистит in-memory кэш от записей старше ``ttl_sec``.

    Возвращает количество удалённых записей. Безопасно вызывать как из
    фоновой задачи ``clear_expired_cache_loop``, так и из тестов (имеет
    предсказуемый side-effect)."""

    now = time.monotonic()
    expired: list[tuple[str, int]] = []
    for task_id, ts in list(_TS.items()):
        if now - ts < ttl_sec:
            continue
        entry = _BY_TASK.get(task_id)
        user_id = entry.user_id if entry else 0
        expired.append((task_id, user_id))

    for task_id, user_id in expired:
        _BY_TASK.pop(task_id, None)
        _TS.pop(task_id, None)
        if user_id and _BY_USER.get(user_id) and _BY_USER[user_id].task_id == task_id:
            _BY_USER.pop(user_id, None)

    if expired:
        logger.info("share_cache: gc removed=%s entries", len(expired))
    return len(expired)


async def clear_expired_cache_loop(
    *,
    ttl_sec: float = SHARE_CACHE_TTL_SEC,
    interval_sec: float = GC_INTERVAL_SEC,
) -> None:
    """Фоновая задача-GC: каждые ``interval_sec`` чистит истёкшие записи.

    Запускается из ``main.py``/``platforms.telegram_bot`` через
    ``asyncio.create_task``. Цикл не падает на единичных ошибках — даже
    если purge подбросит исключение, GC встанет на следующий tick.
    """

    logger.info(
        "share_cache: gc loop started ttl=%ss interval=%ss",
        int(ttl_sec),
        int(interval_sec),
    )
    while True:
        try:
            purge_expired(ttl_sec=ttl_sec)
        except Exception:
            logger.exception("share_cache: gc tick failed")
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            logger.info("share_cache: gc loop cancelled")
            raise


__all__ = (
    "ShareMediaEntry",
    "MediaTaskType",
    "SHARE_CACHE_TTL_SEC",
    "GC_INTERVAL_SEC",
    "remember",
    "get_by_user",
    "get_by_task",
    "clear",
    "purge_expired",
    "clear_expired_cache_loop",
)
