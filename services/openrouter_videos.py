"""OpenRouter Video Generation API (`POST/GET /api/v1/videos`) — оживление фото."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import Settings
from services.api_resilience import ExternalApiError, clip_error_text
from services.openrouter_images import openrouter_images_configured
from services.replicate_client import telegram_photo_download_url

logger = logging.getLogger(__name__)

OPENROUTER_VIDEOS_URL = "https://openrouter.ai/api/v1/videos"
OPENROUTER_VIDEOS_ORIGIN = "https://openrouter.ai"

OPENROUTER_ANIMATE_VIDEO_MODEL = "bytedance/seedance-2.0-mini"

ANIMATE_DEFAULT_PROMPT = (
    "Cinematic subtle portrait movement, realistic eyes blinking, natural gentle breathing, "
    "slight lifelike facial expression, high-quality rendering, "
    "maintain original skin texture and lighting"
)

_TERMINAL_FAILURE_STATUSES = frozenset({"failed", "cancelled", "expired"})


def openrouter_videos_configured(settings: Settings) -> bool:
    """Тот же ключ, что и для Images/Chat — ``OPENROUTER_API_KEY``."""
    return openrouter_images_configured(settings)


def build_frame_images(first_frame_url: str) -> list[dict[str, str]]:
    """OpenRouter Video Cookbook: одна фотография как first frame."""
    url = (first_frame_url or "").strip()
    if not url:
        raise ExternalApiError("OpenRouter", "empty frame image URL")
    return [{"first_frame": url}]


def _resolve_animate_model(settings: Settings) -> str:
    return (
        getattr(settings, "openrouter_animate_video_model", None)
        or OPENROUTER_ANIMATE_VIDEO_MODEL
    ).strip() or OPENROUTER_ANIMATE_VIDEO_MODEL


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _resolve_polling_url(job_payload: dict[str, Any]) -> str:
    raw = str(job_payload.get("polling_url") or "").strip()
    job_id = str(job_payload.get("id") or "").strip()
    if raw:
        if raw.startswith(("http://", "https://")):
            return raw
        return f"{OPENROUTER_VIDEOS_ORIGIN}{raw if raw.startswith('/') else '/' + raw}"
    if job_id:
        return f"{OPENROUTER_VIDEOS_URL}/{job_id}"
    raise ExternalApiError("OpenRouter", "video job missing polling_url and id")


def _extract_video_mp4_url(payload: dict[str, Any]) -> str | None:
    unsigned = payload.get("unsigned_urls")
    if isinstance(unsigned, list):
        for item in unsigned:
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                return item
    for key in ("video_url", "url", "output"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.startswith(("http://", "https://")):
            return raw
    return None


def _http_error_from_response(response: httpx.Response, *, phase: str) -> ExternalApiError:
    text = response.text or ""
    snippet = clip_error_text(text[:4000] or f"HTTP {response.status_code}")
    return ExternalApiError("OpenRouter", f"{phase} HTTP {response.status_code}: {snippet}")


async def _resolve_photo_url(bot: Any, photo_ref: str) -> str:
    ref = (photo_ref or "").strip()
    if ref.startswith(("http://", "https://")):
        return ref
    return await telegram_photo_download_url(bot, ref)


async def generate_openrouter_animate_video(
    settings: Settings,
    *,
    bot: Any,
    telegram_file_id: str,
    prompt: str | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration: int = 5,
) -> str:
    """
    Image-to-video через OpenRouter: submit → poll (18s) → URL готового .mp4.
    Модель по умолчанию: ``bytedance/seedance-2.0-mini``.
    """
    if not openrouter_videos_configured(settings):
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    file_id = (telegram_file_id or "").strip()
    if not file_id:
        raise ExternalApiError("OpenRouter", "empty telegram_file_id")

    cleaned_prompt = (prompt or ANIMATE_DEFAULT_PROMPT).strip()
    image_url = await _resolve_photo_url(bot, file_id)
    frame_images = build_frame_images(image_url)

    from services.billing.chat_pipeline import _collect_openrouter_keys

    api_keys = _collect_openrouter_keys(settings)
    if not api_keys:
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    models = (_resolve_animate_model(settings),)
    poll_interval = float(getattr(settings, "openrouter_video_poll_interval_sec", 18.0) or 18.0)
    poll_timeout = float(getattr(settings, "openrouter_video_poll_timeout_sec", 600.0) or 600.0)
    submit_timeout = min(120.0, poll_timeout)

    try:
        from services.openrouter_http import get_openrouter_http_client

        client = await get_openrouter_http_client(settings)
    except httpx.HTTPError as exc:
        raise ExternalApiError("OpenRouter", clip_error_text(exc)) from exc

    body_base = {
        "prompt": cleaned_prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration": duration,
        "frame_images": frame_images,
        "generate_audio": False,
    }

    last_exc: ExternalApiError | None = None

    for model_id in models:
        body = {**body_base, "model": model_id}

        for key_idx, api_key in enumerate(api_keys):
            try:
                response = await client.post(
                    OPENROUTER_VIDEOS_URL,
                    headers=_auth_headers(api_key),
                    json=body,
                    timeout=httpx.Timeout(submit_timeout, connect=30.0),
                )
            except httpx.HTTPError as exc:
                last_exc = ExternalApiError("OpenRouter", clip_error_text(exc))
                if key_idx + 1 < len(api_keys):
                    logger.warning(
                        "OpenRouter video submit HTTP error key=...%s — next key",
                        api_key[-6:],
                    )
                    continue
                raise last_exc from exc

            if response.status_code in (402, 429):
                last_exc = _http_error_from_response(response, phase="video submit")
                if key_idx + 1 < len(api_keys):
                    logger.warning(
                        "OpenRouter video %s on key ...%s — next key",
                        response.status_code,
                        api_key[-6:],
                    )
                    continue
                raise last_exc

            if response.status_code >= 400:
                last_exc = _http_error_from_response(response, phase="video submit")
                raise last_exc

            try:
                job = response.json()
            except ValueError as exc:
                raise ExternalApiError("OpenRouter", "invalid JSON on video submit") from exc
            if not isinstance(job, dict):
                raise ExternalApiError("OpenRouter", "video submit response is not an object")

            logger.info(
                "OpenRouter video submitted model=%s job_id=%s key=...%s",
                model_id,
                job.get("id"),
                api_key[-6:],
            )
            return await _poll_video_job(
                client,
                api_key=api_key,
                job_payload=job,
                poll_interval_sec=poll_interval,
                poll_timeout_sec=poll_timeout,
            )

    if last_exc is not None:
        raise last_exc
    raise ExternalApiError("OpenRouter", "video generation failed")


async def _poll_video_job(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    job_payload: dict[str, Any],
    poll_interval_sec: float,
    poll_timeout_sec: float,
) -> str:
    polling_url = _resolve_polling_url(job_payload)
    headers = _auth_headers(api_key)
    deadline = asyncio.get_running_loop().time() + poll_timeout_sec
    current = job_payload

    while True:
        status = str(current.get("status") or "pending").strip().lower()
        if status == "completed":
            mp4_url = _extract_video_mp4_url(current)
            if mp4_url:
                return mp4_url
            raise ExternalApiError("OpenRouter", "video completed without unsigned_urls")

        if status in _TERMINAL_FAILURE_STATUSES:
            err = clip_error_text(str(current.get("error") or f"video job {status}"))
            raise ExternalApiError("OpenRouter", err)

        if asyncio.get_running_loop().time() >= deadline:
            raise ExternalApiError("OpenRouter", "video polling timeout")

        await asyncio.sleep(poll_interval_sec)

        poll_resp = await client.get(
            polling_url,
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=15.0),
        )
        if poll_resp.status_code != 200:
            raise _http_error_from_response(poll_resp, phase="video poll")

        try:
            current = poll_resp.json()
        except ValueError as exc:
            raise ExternalApiError("OpenRouter", "invalid JSON on video poll") from exc
        if not isinstance(current, dict):
            raise ExternalApiError("OpenRouter", "video poll response is not an object")
