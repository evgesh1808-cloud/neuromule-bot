"""Клиент Suno API (или совместимого прокси): генерация трека по тексту стиля.

Поддерживает 3 режима NeuroMule 🐎⚡️:

* ``ИИ пишет текст + Стиль`` — отправляем только ``prompt``.
* ``Свой текст`` — отправляем ``lyrics`` + ``prompt`` (стиль), Suno
  выставляет ``custom_mode`` сам по наличию lyrics.
* ``Только музыка (минус)`` — ``make_instrumental=True``.

Парсер ответа специально устойчивый: рекурсивно ищет ``audio_url``/
``audioUrl``/``url`` и ``clip_id``/``id`` в плоских или вложенных
структурах ответа разных провайдеров Suno API. Любой сбой сети, пустой
URL или некорректный JSON — ``None``, что в воркере мгновенно приводит к
рефанду 15 💎 по ``billing_charge_id``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIXES = ("your_", "ваш_", "your-")
_URL_KEYS = ("audio_url", "audioUrl", "url", "stream_audio_url", "streamAudioUrl")
_CLIP_KEYS = ("clip_id", "clipId", "id", "song_id")
_TITLE_KEYS = ("title", "name", "song_name")
_LIST_KEYS = ("data", "clips", "songs", "result", "output", "tracks", "items")


@dataclass
class SunoTrack:
    """Один сгенерированный трек: URL, заголовок и clip_id для extend."""

    audio_url: str
    title: str
    clip_id: str | None = None


def suno_configured() -> bool:
    token = (settings.suno_api_token or "").strip()
    if not token:
        return False
    low = token.lower()
    return not any(low.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def _api_base() -> str:
    return (settings.suno_api_url or "").strip().rstrip("/")


def _generate_url() -> str:
    base = _api_base()
    if not base:
        return ""
    return urljoin(f"{base}/", "generate")


def _first_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _find_track(node: Any, depth: int = 0) -> SunoTrack | None:
    """Рекурсивный обход payload в поисках первой записи с валидным URL."""

    if depth > 6:
        return None

    if isinstance(node, dict):
        url = _first_str(node, _URL_KEYS)
        if url and url.startswith(("http://", "https://")):
            title = _first_str(node, _TITLE_KEYS) or "ИИ-сингл NeuroMule 🐎"
            clip_id = _first_str(node, _CLIP_KEYS)
            return SunoTrack(audio_url=url, title=title[:64], clip_id=clip_id)

        for key in _LIST_KEYS:
            sub = node.get(key)
            track = _find_track(sub, depth + 1)
            if track is not None:
                return track

        for value in node.values():
            track = _find_track(value, depth + 1)
            if track is not None:
                return track

    elif isinstance(node, list):
        for item in node:
            track = _find_track(item, depth + 1)
            if track is not None:
                return track

    return None


def _parse_track_payload(data: Any) -> tuple[str, str] | None:
    """Совместимая обёртка над :func:`_find_track` (без ``clip_id``)."""
    track = _find_track(data)
    if track is None:
        return None
    return track.audio_url, track.title


async def generate_music_track(
    prompt: str,
    *,
    lyrics: str | None = None,
    make_instrumental: bool | None = None,
    wait_audio: bool | None = None,
    continue_clip_id: str | None = None,
) -> SunoTrack | None:
    """POST ``{SUNO_API_URL}/generate`` с Bearer-токеном.

    Возвращает :class:`SunoTrack` (URL, title, clip_id) или ``None`` при
    любой ошибке. ``None`` означает, что воркер должен запустить рефанд.
    """

    if not suno_configured():
        logger.error("Suno: SUNO_API_TOKEN не задан")
        return None

    style = (prompt or "").strip()
    if not style:
        logger.error("Suno: пустой prompt")
        return None

    url = _generate_url()
    if not url:
        logger.error("Suno: SUNO_API_URL не задан")
        return None

    instrumental = (
        settings.suno_make_instrumental if make_instrumental is None else make_instrumental
    )
    wait = settings.suno_wait_audio if wait_audio is None else wait_audio

    payload: dict[str, Any] = {
        "prompt": style,
        "make_instrumental": bool(instrumental),
        "wait_audio": bool(wait),
    }

    cleaned_lyrics = (lyrics or "").strip()
    if cleaned_lyrics and not instrumental:
        # «Свой текст»: совместимость с распространёнными форматами прокси.
        payload["lyrics"] = cleaned_lyrics
        payload["custom_mode"] = True
        payload["tags"] = style  # часть прокси берут стиль из tags при custom_mode
        payload["title"] = "NeuroMule"

    cleaned_clip = (continue_clip_id or "").strip()
    if cleaned_clip:
        payload["continue_clip_id"] = cleaned_clip
        payload["continue_at"] = "auto"

    headers = {
        "Authorization": f"Bearer {settings.suno_api_token.strip()}",
        "Content-Type": "application/json",
    }

    try:
        timeout = httpx.Timeout(settings.suno_request_timeout_sec, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error("Suno API %s: %s", resp.status_code, resp.text[:500])
                return None
            try:
                data = resp.json()
            except ValueError:
                logger.error("Suno: ответ не JSON: %s", resp.text[:200])
                return None
            track = _find_track(data)
            if track is None:
                logger.error("Suno: не найден audio_url в ответе")
            return track
    except Exception:
        logger.exception("Suno generate failed prompt_len=%s", len(style))
        return None
