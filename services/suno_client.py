"""Клиент Suno API (или совместимого прокси): генерация трека по тексту стиля."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from config import settings

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIXES = ("your_", "ваш_", "your-")


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


def _parse_track_payload(data: Any) -> tuple[str, str] | None:
    """Из ответа прокси/Suno: (audio_url, title)."""
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("data", "clips", "songs", "result", "output"):
            block = data.get(key)
            if isinstance(block, list) and block:
                items = block
                break
        else:
            items = [data]
    else:
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("audio_url") or item.get("audioUrl") or item.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            title = str(item.get("title") or item.get("name") or "ИИ-сингл NeuroMul")
            return url, title[:64]
    return None


async def generate_music_track(
    prompt: str,
    *,
    make_instrumental: bool | None = None,
    wait_audio: bool | None = None,
) -> tuple[str, str] | None:
    """
    POST ``{SUNO_API_URL}/generate`` с Bearer-токеном.

    Возвращает ``(audio_url, title)`` или ``None``.
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

    payload = {
        "prompt": style,
        "make_instrumental": instrumental,
        "wait_audio": wait,
    }
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
            parsed = _parse_track_payload(resp.json())
            if not parsed:
                logger.error("Suno: не найден audio_url в ответе")
            return parsed
    except Exception:
        logger.exception("Suno generate failed prompt_len=%s", len(style))
        return None
