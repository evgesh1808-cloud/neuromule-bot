"""Единое ядро WebApp Galleryи NeuroMule 🐎⚡️ (one-core, multi-displays).

Этот модуль — **единственная** точка истины для Mini App (Telegram Mini App,
VK Mini App, MAX Messenger WebApp). Никаких трёх отдельных бэкендов: все
три витрины читают одну и ту же SQLite-таблицу ``webapp_gallery`` и
рендерят её фронт-кодом, адаптированным под платформу.

Что хранит запись (после явного двойного согласия юзера в боте):

* ``task_id`` — уникальный id задачи генерации (источник для шеринга).
* ``user_id`` — автор (виден только внутри БД, наружу не отдаётся).
* ``task_type`` — ``photo`` / ``video`` / ``animate`` / ``music``.
* ``prompt`` — пользовательский ИИ-промпт (анонимный текст).
* ``media_url`` — прямая ссылка на медиа (``api.telegram.org/file/...`` или
  оригинальный URL внешнего API Replicate/Suno/Imagen, что прилетел в
  ``last_share_media``).
* ``hashtag`` — тематический хэштег-рубрикатор (``#gallery_flux`` и т.п.).
* ``created_at`` — ISO-таймстемп публикации.
* ``is_visible`` — флаг видимости (``1`` по умолчанию, ``0`` после
  отзыва согласия по требованию автора).

API:

* :func:`ensure_schema` — идемпотентная миграция таблицы.
* :func:`publish_to_gallery` — атомарная запись новой публикации.
* :func:`list_recent_publications` — выборка для фронта Mini App.
* :func:`hide_publication` — мягкое скрытие при отзыве согласия автором.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import aiosqlite

from content import messages as msg
from services import repository

logger = logging.getLogger(__name__)


TaskType = Literal["photo", "video", "animate", "music"]


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS webapp_gallery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    task_type TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    media_url TEXT NOT NULL,
    hashtag TEXT NOT NULL DEFAULT '#NeuroMule',
    created_at TEXT NOT NULL,
    is_visible INTEGER NOT NULL DEFAULT 1
)
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_webapp_gallery_visible_created "
    "ON webapp_gallery (is_visible, created_at DESC)"
)


@dataclass(frozen=True)
class GalleryItem:
    """Read-модель для фронта Mini App."""

    task_id: str
    task_type: TaskType
    prompt: str
    media_url: str
    hashtag: str
    created_at: str


def _hashtag_for(task_type: TaskType) -> str:
    return msg.GALLERY_HASHTAGS.get(task_type, "#NeuroMule")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ensure_schema() -> None:
    """Создаёт таблицу и индекс при первом обращении. Идемпотентно."""

    async with aiosqlite.connect(repository.DB_PATH) as db:
        await db.execute(_SCHEMA_DDL)
        await db.execute(_INDEX_DDL)
        await db.commit()


async def publish_to_gallery(
    *,
    task_id: str,
    user_id: int,
    task_type: TaskType,
    prompt: str,
    media_url: str,
) -> bool:
    """Атомарная запись новой публикации.

    Возвращает ``True`` при успехе. ``False`` — если ``task_id`` уже есть в
    таблице (двойной клик пользователя по «Опубликовать»).
    """

    if not media_url:
        return False
    await ensure_schema()
    hashtag = _hashtag_for(task_type)
    text = (prompt or "").strip()
    try:
        async with aiosqlite.connect(repository.DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO webapp_gallery (
                    task_id, user_id, task_type, prompt, media_url,
                    hashtag, created_at, is_visible
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    str(task_id),
                    int(user_id),
                    task_type,
                    text,
                    media_url,
                    hashtag,
                    _now_iso(),
                ),
            )
            await db.commit()
        logger.info(
            "webapp_gallery: published task=%s user=%s type=%s",
            task_id,
            user_id,
            task_type,
        )
        return True
    except aiosqlite.IntegrityError:
        logger.info("webapp_gallery: duplicate task=%s — skipped", task_id)
        return False
    except Exception:
        logger.exception("webapp_gallery: publish failed task=%s", task_id)
        return False


async def list_recent_publications(
    *,
    limit: int = 50,
    task_type: TaskType | None = None,
) -> list[GalleryItem]:
    """Выборка последних публикаций для рендера Mini App.

    Атрибут ``user_id`` намеренно НЕ возвращается — фронт получает только
    анонимные поля (анонимность гарантирована UX-щлагбаумом в боте).
    """

    await ensure_schema()
    sql = (
        "SELECT task_id, task_type, prompt, media_url, hashtag, created_at "
        "FROM webapp_gallery WHERE is_visible = 1"
    )
    params: list = []
    if task_type is not None:
        sql += " AND task_type = ?"
        params.append(task_type)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, int(limit)))

    async with aiosqlite.connect(repository.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()

    return [
        GalleryItem(
            task_id=str(r["task_id"]),
            task_type=str(r["task_type"]),  # type: ignore[arg-type]
            prompt=str(r["prompt"] or ""),
            media_url=str(r["media_url"]),
            hashtag=str(r["hashtag"]),
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


async def hide_publication(task_id: str) -> bool:
    """Мягкое скрытие публикации (отзыв согласия по запросу автора)."""

    await ensure_schema()
    async with aiosqlite.connect(repository.DB_PATH) as db:
        cur = await db.execute(
            "UPDATE webapp_gallery SET is_visible = 0 WHERE task_id = ?",
            (str(task_id),),
        )
        await db.commit()
        return cur.rowcount > 0


__all__ = (
    "TaskType",
    "GalleryItem",
    "ensure_schema",
    "publish_to_gallery",
    "list_recent_publications",
    "hide_publication",
)
