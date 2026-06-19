"""Кросс-пост в MAX App (NeuroMule 🐎⚡️ • feed коротких видео).

API MAX App ожидает multipart/form-data POST на эндпоинт
``settings.max_api_url`` с авторизацией по ``Bearer settings.max_api_token``.
Поле ``video`` — бинарные байты файла, ``caption`` — описание, ``source_link``
— ссылка обратного захвата трафика в Telegram-бота.

Принимаем только task'и с готовым прямым URL (``media_url``) — это типичный
случай Replicate/Suno. Скачиваем контент httpx'ом и проксируем дальше.
Любые сбои (таймаут, не-2xx, отсутствие токена) → ``False`` без падений.
"""

from __future__ import annotations

import logging

import httpx

from config import settings as app_settings
from services.last_share_media import ShareMediaEntry

logger = logging.getLogger(__name__)


MAX_HTTP_TIMEOUT_SEC = 30.0
MAX_UPLOAD_BYTES = 80_000_000  # 80 MB — защита от мегабайт-бомб


def max_app_configured() -> bool:
    token = (app_settings.max_api_token or "").strip()
    return bool(token) and bool((app_settings.max_api_url or "").strip())


def _build_caption(entry: ShareMediaEntry) -> str:
    username = (app_settings.telegram_bot_username or "NeuroMule_bot").lstrip("@")
    prompt = entry.prompt[:600] or "Шедевр NeuroMule 🐎⚡️"
    return (
        f"{prompt}\n\n"
        f"🤖 t.me/{username} #NeuroMule #MAX_ИИ"
    )


def _source_link() -> str:
    username = (app_settings.telegram_bot_username or "NeuroMule_bot").lstrip("@")
    return f"https://t.me/{username}?start=maxapp"


def _is_video_kind(entry: ShareMediaEntry) -> bool:
    """MAX App принимает только видео-поток (включая music-clip MP4)."""

    return entry.task_type in ("video", "animate", "music")


async def cross_post_to_max_app(
    entry: ShareMediaEntry,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """Заливает видео в feed MAX App. Возвращает ``True`` при успехе."""

    if not max_app_configured():
        logger.info("max_app: not configured, skip task=%s", entry.task_id)
        return False
    if not _is_video_kind(entry):
        logger.info(
            "max_app: skip non-video task type=%s task=%s",
            entry.task_type,
            entry.task_id,
        )
        return False
    if not entry.media_url:
        logger.info("max_app: no media_url to fetch task=%s", entry.task_id)
        return False

    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=MAX_HTTP_TIMEOUT_SEC)
    try:
        try:
            video_resp = await client.get(entry.media_url, timeout=MAX_HTTP_TIMEOUT_SEC)
        except Exception:
            logger.exception("max_app: fetch source failed task=%s", entry.task_id)
            return False
        if video_resp.status_code != 200 or not video_resp.content:
            return False
        if len(video_resp.content) > MAX_UPLOAD_BYTES:
            logger.warning(
                "max_app: source too big %s bytes task=%s",
                len(video_resp.content),
                entry.task_id,
            )
            return False

        files = {
            "video": (
                f"{entry.task_id}.mp4",
                video_resp.content,
                "video/mp4",
            )
        }
        data = {
            "caption": _build_caption(entry),
            "source_link": _source_link(),
            "hashtags": "#NeuroMule #MAX_ИИ",
        }
        headers = {"Authorization": f"Bearer {app_settings.max_api_token.strip()}"}

        try:
            upload_resp = await client.post(
                app_settings.max_api_url.strip(),
                data=data,
                files=files,
                headers=headers,
                timeout=MAX_HTTP_TIMEOUT_SEC,
            )
        except Exception:
            logger.exception("max_app: upload POST failed task=%s", entry.task_id)
            return False

        if upload_resp.status_code not in (200, 201, 202):
            logger.warning(
                "max_app: upload status=%s body=%s task=%s",
                upload_resp.status_code,
                upload_resp.text[:300],
                entry.task_id,
            )
            return False
        return True
    finally:
        if own:
            await client.aclose()


__all__ = ("max_app_configured", "cross_post_to_max_app")
