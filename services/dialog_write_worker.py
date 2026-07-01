"""
Фоновый воркер: очередь коммитов «assistant + prune» в SQLite.

Один asyncio-цикл снимает задачи с очереди и пишет в БД последовательно — меньше SQLITE_BUSY,
чем параллельные ``connect`` из разных хендлеров. Если воркер не запущен (тесты), используется прямой коммит.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_FALLBACK_ASSISTANT_TEXT = "ℹ️ Технический Fast-Path отчет без текстового анализа ИИ."

_queue: asyncio.Queue["_CommitJob"] | None = None
_worker_task: asyncio.Task[None] | None = None
_worker_lock = asyncio.Lock()


@dataclass
class _CommitJob:
    user_id: int
    assistant_text: str
    prune_keep: int
    platform: str
    done: asyncio.Future[None]


def _normalize_assistant_text(text: str | None) -> str:
    """Гарантирует NOT NULL для ``dialog_messages.content``."""
    cleaned = (text or "").strip()
    return cleaned if cleaned else _FALLBACK_ASSISTANT_TEXT


async def start_dialog_write_worker() -> None:
    """
    Запускает фоновую задачу обработки очереди (идемпотентно: повторный вызов не дублирует воркер).

    Вызывать из ``run_telegram`` после ``init_db``.
    """
    from config import settings

    global _queue, _worker_task
    if not settings.dialog_write_worker_enabled:
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _queue = asyncio.Queue(maxsize=0)

    async def _run() -> None:
        from services import repository as repo

        logger.info("dialog_write_worker: started")
        while True:
            job = await _queue.get()
            try:
                text_to_save = _normalize_assistant_text(job.assistant_text)
                async with _worker_lock:
                    await repo.dialog_append(
                        job.user_id,
                        "assistant",
                        text_to_save,
                        platform=job.platform,
                    )
                    await repo.dialog_prune_keep_last(
                        job.user_id,
                        job.prune_keep,
                        platform=job.platform,
                    )
                if not job.done.done():
                    job.done.set_result(None)
            except Exception as e:
                logger.error(
                    "dialog_write_worker: commit failed user_id=%s",
                    job.user_id,
                    exc_info=True,
                )
                if not job.done.done():
                    job.done.set_exception(e)
            finally:
                _queue.task_done()

    _worker_task = asyncio.create_task(_run(), name="dialog_write_worker")


async def commit_assistant_turn_queued(
    user_id: int,
    assistant_text: str | None,
    prune_keep: int,
    *,
    platform: str | None = None,
) -> None:
    """
    Сохраняет реплику ассистента и prune: через очередь воркера или напрямую (если воркер не запущен).

    Вызывающий **await**-ит завершение записи — история в БД согласована до возврата из use-case.
    """
    from config import settings

    from services.dialog_platform import DEFAULT_DIALOG_PLATFORM

    assistant_text = _normalize_assistant_text(assistant_text)
    platform_key = platform or DEFAULT_DIALOG_PLATFORM

    if not settings.dialog_write_worker_enabled or _queue is None:
        from services.dialog_history import serialized_assistant_commit
        from services.repository import dialog_append, dialog_prune_keep_last

        async with serialized_assistant_commit():
            await dialog_append(user_id, "assistant", assistant_text, platform=platform_key)
            await dialog_prune_keep_last(user_id, prune_keep, platform=platform_key)
        return

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[None] = loop.create_future()
    await _queue.put(_CommitJob(user_id, assistant_text, prune_keep, platform_key, fut))
    await fut
