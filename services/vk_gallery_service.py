"""Кросс-пост шедевров в публичную VK-группу (NeuroMule 🐎⚡️).

VK API v5.131:

* Фото → ``photos.getUploadServer(album_id, group_id)`` →
  multipart-upload изображения → ``photos.save`` → ``wall.post`` с
  ``attachments=photo<owner>_<id>``.
* Видео → ``video.save(group_id, album_id)`` возвращает upload_url →
  multipart POST содержимого → ``wall.post`` со ссылкой на видеозапись.
* Музыка → крепится как audio (``wall.post`` с ``message`` и audio attach).

Реальная загрузка медиа требует исходный байт-поток. Этот модуль
рассчитывает на готовый прямой URL (``media_url``) — он скачивает контент
через ``httpx`` асинхронно и проксирует на VK.

Если ``vk_group_token`` пустой — модуль ``not configured()`` и любая публикация
короткозамкнётся на ``False`` без падений.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from services.streaming_download import stream_download_to_bytes

from config import settings as app_settings
from services.last_share_media import ShareMediaEntry

logger = logging.getLogger(__name__)

VK_API_VERSION = "5.131"
VK_API_BASE = "https://api.vk.com/method"
VK_REQUEST_TIMEOUT_SEC = 15.0
VK_HTTP_TIMEOUT_SEC = 30.0


def vk_configured() -> bool:
    token = (app_settings.vk_group_token or "").strip()
    return bool(token) and int(app_settings.vk_group_id or 0) > 0


def _build_caption(entry: ShareMediaEntry) -> str:
    prompt = entry.prompt[:900] or "Шедевр NeuroMule 🐎⚡️"
    return (
        f"🎨 NeuroMule 2026 — ИИ-шедевр\n\n"
        f"{prompt}\n\n"
        f"⚡ Создай свой: {app_settings.vk_share_short_url}\n"
        f"#NeuroMule"
    )


async def _vk_call(
    method: str,
    params: dict[str, Any],
    *,
    client: httpx.AsyncClient,
) -> dict[str, Any] | None:
    """Тонкая обёртка вызова VK API: добавляет токен/версию, гасит ошибки."""

    payload = {
        "access_token": app_settings.vk_group_token.strip(),
        "v": VK_API_VERSION,
        **params,
    }
    try:
        resp = await client.post(
            f"{VK_API_BASE}/{method}",
            data=payload,
            timeout=VK_REQUEST_TIMEOUT_SEC,
        )
    except Exception:
        logger.exception("vk: HTTP failed method=%s", method)
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.warning("vk: non-json response method=%s body=%s", method, resp.text[:200])
        return None
    if isinstance(data, dict) and data.get("error"):
        logger.warning("vk: api error method=%s err=%s", method, data["error"])
        return None
    return data if isinstance(data, dict) else None


async def _upload_photo_via_url(
    *,
    media_url: str,
    album_id: int,
    client: httpx.AsyncClient,
) -> str | None:
    """Заливает фото по URL: getUploadServer → POST file → photos.save."""

    get_upload = await _vk_call(
        "photos.getUploadServer",
        {"album_id": album_id, "group_id": app_settings.vk_group_id},
        client=client,
    )
    upload_url = (get_upload or {}).get("response", {}).get("upload_url")
    if not upload_url:
        return None

    # Streaming-загрузка с жёстким лимитом (PR-J): не таскаем всё фото в RAM.
    # 20 МБ — выше типичного размера Telegram/Replicate-фото, ниже VK-лимита.
    img_bytes = await stream_download_to_bytes(
        client, media_url, source="vk_photo"
    )
    if img_bytes is None:
        return None

    try:
        files = {"file1": ("photo.jpg", img_bytes, "image/jpeg")}
        up_resp = await client.post(
            upload_url, files=files, timeout=VK_HTTP_TIMEOUT_SEC
        )
        upload_payload = up_resp.json()
    except httpx.HTTPError:
        logger.warning(
            "vk: photo upload POST failed", exc_info=True
        )
        return None
    except ValueError:  # not JSON
        logger.warning("vk: photo upload returned non-json response")
        return None

    save = await _vk_call(
        "photos.save",
        {
            "album_id": album_id,
            "group_id": app_settings.vk_group_id,
            "server": upload_payload.get("server"),
            "photos_list": upload_payload.get("photos_list"),
            "hash": upload_payload.get("hash"),
        },
        client=client,
    )
    items = (save or {}).get("response") or []
    if not items:
        return None
    first = items[0]
    return f"photo{first.get('owner_id')}_{first.get('id')}"


async def _wall_post(
    *,
    attachments: str | None,
    message: str,
    client: httpx.AsyncClient,
) -> bool:
    params: dict[str, Any] = {
        "owner_id": -int(app_settings.vk_group_id),
        "from_group": 1,
        "message": message,
    }
    if attachments:
        params["attachments"] = attachments
    out = await _vk_call("wall.post", params, client=client)
    return bool((out or {}).get("response", {}).get("post_id"))


async def post_photo_to_vk(
    entry: ShareMediaEntry,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """Заливает фото в фотоальбом и постит ссылку-вложение на стену."""

    if not vk_configured() or not entry.media_url:
        return False

    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=VK_HTTP_TIMEOUT_SEC)
    try:
        attach = await _upload_photo_via_url(
            media_url=entry.media_url,
            album_id=int(app_settings.vk_photo_album_id or 0),
            client=client,
        )
        if not attach:
            return False
        return await _wall_post(
            attachments=attach,
            message=_build_caption(entry),
            client=client,
        )
    except Exception:
        logger.exception("vk: photo post failed task=%s", entry.task_id)
        return False
    finally:
        if own:
            await client.aclose()


async def post_video_to_vk(
    entry: ShareMediaEntry,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """MVP: оставляем ссылку в тексте поста. Полная загрузка видео требует
    отдельного VK upload-сервера и multipart-стрима; реализуем по мере роста."""

    if not vk_configured() or not entry.media_url:
        return False

    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=VK_HTTP_TIMEOUT_SEC)
    try:
        body = _build_caption(entry) + f"\n\n🎬 Видео: {entry.media_url}"
        return await _wall_post(attachments=None, message=body, client=client)
    except Exception:
        logger.exception("vk: video post failed task=%s", entry.task_id)
        return False
    finally:
        if own:
            await client.aclose()


async def post_music_to_vk(
    entry: ShareMediaEntry,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """MVP: вешает ссылку на трек в тексте поста с тегом #NeuroMule_Music."""

    if not vk_configured() or not entry.media_url:
        return False

    own = http_client is None
    client = http_client or httpx.AsyncClient(timeout=VK_HTTP_TIMEOUT_SEC)
    try:
        body = (
            _build_caption(entry)
            + f"\n\n🎸 Слушать: {entry.media_url}\n#NeuroMule_Music"
        )
        return await _wall_post(attachments=None, message=body, client=client)
    except Exception:
        logger.exception("vk: music post failed task=%s", entry.task_id)
        return False
    finally:
        if own:
            await client.aclose()


async def cross_post_to_vk(
    entry: ShareMediaEntry,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """Router: photo/video/music → соответствующий аплоадер."""

    if entry.task_type == "photo":
        return await post_photo_to_vk(entry, http_client=http_client)
    if entry.task_type in ("video", "animate"):
        return await post_video_to_vk(entry, http_client=http_client)
    if entry.task_type == "music":
        return await post_music_to_vk(entry, http_client=http_client)
    return False


__all__ = (
    "vk_configured",
    "cross_post_to_vk",
    "post_photo_to_vk",
    "post_video_to_vk",
    "post_music_to_vk",
)
