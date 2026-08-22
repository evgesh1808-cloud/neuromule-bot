"""OpenRouter Video Generation API (`POST/GET /api/v1/videos`) — оживление фото."""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import httpx

from config import Settings
from services.api_resilience import ExternalApiError, clip_error_text
from services.openrouter_images import openrouter_images_configured
from services.streaming_download import DEFAULT_MAX_BYTES, stream_download_to_bytes

logger = logging.getLogger(__name__)

OPENROUTER_VIDEOS_URL = "https://openrouter.ai/api/v1/videos"
OPENROUTER_VIDEOS_ORIGIN = "https://openrouter.ai"

OPENROUTER_ANIMATE_VIDEO_MODEL = "bytedance/seedance-2.0-mini"
ANIMATE_FRAME_MAX_BYTES = DEFAULT_MAX_BYTES
ANIMATE_VIDEO_MAX_BYTES = 50 * 1024 * 1024

ANIMATE_DEFAULT_PROMPT = (
    "Cinematic subtle portrait movement, realistic eyes blinking, natural gentle breathing, "
    "slight lifelike facial expression, high-quality rendering, "
    "maintain original skin texture and lighting"
)

DEFAULT_ANIMATE_DURATION_SEC = 5
VEO_ANIMATE_DURATION_SEC = 4


@dataclass(frozen=True, slots=True)
class OpenRouterAnimateResult:
    """URL готового mp4 и ключ OpenRouter, которым job был создан."""

    url: str
    api_key: str


def resolve_animate_duration_for_model(model_id: str) -> int:
    """Veo принимает 4/6/8 с; Seedance — 4–15. Безопасный дефолт — 4 для Veo, 5 для остальных."""
    mid = (model_id or "").strip().lower()
    if "veo" in mid:
        return VEO_ANIMATE_DURATION_SEC
    return DEFAULT_ANIMATE_DURATION_SEC


def _looks_like_mp4(data: bytes) -> bool:
    raw = bytes(data or b"")
    if len(raw) < 12:
        return False
    return raw[4:8] == b"ftyp"

_TERMINAL_FAILURE_STATUSES = frozenset({"failed", "cancelled", "expired"})
_IMAGE_ERROR_MARKERS = (
    "image",
    "frame",
    "url",
    "download",
    "fetch",
    "invalid",
    "unable to retrieve",
    "could not",
)


def openrouter_videos_configured(settings: Settings) -> bool:
    """Тот же ключ, что и для Images/Chat — ``OPENROUTER_API_KEY``."""
    return openrouter_images_configured(settings)


def build_frame_images(image_url: str) -> list[dict[str, Any]]:
    """OpenRouter Video API: ``FrameImage`` с обязательным ``frame_type``."""
    url = (image_url or "").strip()
    if not url:
        raise ExternalApiError("OpenRouter", "empty frame image URL")
    return [
        {
            "type": "image_url",
            "image_url": {"url": url},
            "frame_type": "first_frame",
        }
    ]


def _resolve_animate_model(settings: Settings) -> str:
    return (
        getattr(settings, "openrouter_animate_video_model", None)
        or OPENROUTER_ANIMATE_VIDEO_MODEL
    ).strip() or OPENROUTER_ANIMATE_VIDEO_MODEL


def _resolve_animate_model_candidates(settings: Settings) -> tuple[str, ...]:
    """Primary + fallback из настроек (без дубликатов)."""
    primary = _resolve_animate_model(settings)
    fallback = (
        getattr(settings, "openrouter_animate_video_fallback_model", None) or ""
    ).strip()
    out: list[str] = []
    for slug in (primary, fallback):
        if slug and slug not in out:
            out.append(slug)
    return tuple(out)


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _is_telegram_bot_file_url(url: str) -> bool:
    return "api.telegram.org/file/bot" in (url or "").lower()


def _is_telegram_file_id(photo_ref: str) -> bool:
    ref = (photo_ref or "").strip()
    return bool(ref) and not ref.startswith(("http://", "https://", "data:"))


def _should_use_data_url_first(photo_ref: str) -> bool:
    ref = (photo_ref or "").strip()
    if not ref:
        return False
    if ref.startswith("data:"):
        return True
    if _is_telegram_file_id(ref):
        return True
    return _is_telegram_bot_file_url(ref)


def _mime_from_path(path: str) -> str:
    low = (path or "").lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".webp"):
        return "image/webp"
    if low.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _bytes_to_data_url(data: bytes, mime: str = "image/jpeg") -> str:
    if not data:
        raise ExternalApiError("OpenRouter", "empty image bytes")
    if len(data) > ANIMATE_FRAME_MAX_BYTES:
        raise ExternalApiError("OpenRouter", "frame image exceeds size limit")
    encoded = base64.standard_b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def _download_telegram_file_id(bot: Any, file_id: str) -> str:
    tg_file = await bot.get_file(file_id)
    if not tg_file or not tg_file.file_path:
        raise ExternalApiError("OpenRouter", "Telegram did not return file_path for photo")
    buffer = BytesIO()
    await bot.download_file(tg_file.file_path, buffer)
    raw = buffer.getvalue()
    return _bytes_to_data_url(raw, _mime_from_path(tg_file.file_path))


async def _download_http_image_to_data_url(settings: Settings, url: str) -> str:
    from services.openrouter_http import get_openrouter_http_client

    client = await get_openrouter_http_client(settings)
    data = await stream_download_to_bytes(
        client,
        url,
        max_bytes=ANIMATE_FRAME_MAX_BYTES,
        source="openrouter_video_frame",
    )
    if not data:
        raise ExternalApiError("OpenRouter", "failed to download frame image")
    mime = _mime_from_path(url)
    return _bytes_to_data_url(data, mime)


async def photo_ref_to_data_url(settings: Settings, bot: Any, photo_ref: str) -> str:
    """Telegram file_id / TG CDN URL / https → inline ``data:*;base64,...``."""
    ref = (photo_ref or "").strip()
    if not ref:
        raise ExternalApiError("OpenRouter", "empty photo reference")
    if ref.startswith("data:"):
        return ref
    if _is_telegram_file_id(ref):
        return await _download_telegram_file_id(bot, ref)
    if ref.startswith(("http://", "https://")):
        return await _download_http_image_to_data_url(settings, ref)
    raise ExternalApiError("OpenRouter", "unsupported photo reference")


async def resolve_frame_image_url(
    settings: Settings,
    bot: Any,
    photo_ref: str,
    *,
    force_data_url: bool = False,
) -> str:
    """
    URL для ``frame_images[].image_url.url``.

    Telegram-источники всегда инлайним в data-URL — OpenRouter не скачивает
    ``api.telegram.org/file/bot/...`` без нашего токена.
    """
    ref = (photo_ref or "").strip()
    if force_data_url or _should_use_data_url_first(ref):
        return await photo_ref_to_data_url(settings, bot, ref)
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    return await photo_ref_to_data_url(settings, bot, ref)


def _response_looks_like_image_error(status_code: int, body: str) -> bool:
    if status_code not in (400, 422):
        return False
    low = (body or "").lower()
    return any(marker in low for marker in _IMAGE_ERROR_MARKERS)


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


async def download_animate_video_bytes(
    settings: Settings,
    mp4_url: str,
    *,
    api_key: str | None = None,
) -> bytes:
    """Скачивает готовый mp4 для ``send_video`` (OpenRouter CDN может требовать Bearer)."""
    from services.openrouter_http import get_openrouter_http_client

    url = (mp4_url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ExternalApiError("OpenRouter", "invalid animate video URL")

    client = await get_openrouter_http_client(settings)
    auth_headers = {"Authorization": f"Bearer {api_key}"} if (api_key or "").strip() else None
    data = await stream_download_to_bytes(
        client,
        url,
        max_bytes=ANIMATE_VIDEO_MAX_BYTES,
        source="openrouter_animate_video",
        headers=auth_headers,
    )
    if not data and auth_headers:
        logger.warning("OpenRouter animate video download without auth fallback url=%s", url[:80])
        data = await stream_download_to_bytes(
            client,
            url,
            max_bytes=ANIMATE_VIDEO_MAX_BYTES,
            source="openrouter_animate_video",
        )
    if not data:
        raise ExternalApiError("OpenRouter", "failed to download animate video")
    if not _looks_like_mp4(data):
        raise ExternalApiError("OpenRouter", "downloaded animate payload is not mp4")
    return data


async def _submit_video_job(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    body: dict[str, Any],
    submit_timeout: float,
) -> httpx.Response:
    return await client.post(
        OPENROUTER_VIDEOS_URL,
        headers=_auth_headers(api_key),
        json=body,
        timeout=httpx.Timeout(submit_timeout, connect=30.0),
    )


async def generate_openrouter_animate_video(
    settings: Settings,
    *,
    bot: Any,
    telegram_file_id: str,
    prompt: str | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    duration: int | None = None,
) -> OpenRouterAnimateResult:
    """
    Image-to-video через OpenRouter: submit → poll (18s) → URL готового .mp4.
    """
    if not openrouter_videos_configured(settings):
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    photo_ref = (telegram_file_id or "").strip()
    if not photo_ref:
        raise ExternalApiError("OpenRouter", "empty telegram_file_id")

    cleaned_prompt = (prompt or ANIMATE_DEFAULT_PROMPT).strip()

    from services.billing.chat_pipeline import _collect_openrouter_keys

    api_keys = _collect_openrouter_keys(settings)
    if not api_keys:
        raise ExternalApiError("OpenRouter", "OPENROUTER_API_KEY не задан")

    model_candidates = _resolve_animate_model_candidates(settings)
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
        "generate_audio": False,
    }

    force_data_modes = (
        [True]
        if _should_use_data_url_first(photo_ref)
        else [False, True]
    )

    last_exc: ExternalApiError | None = None

    for model_idx, model_id in enumerate(model_candidates):
        body_duration = (
            int(duration)
            if duration is not None
            else resolve_animate_duration_for_model(model_id)
        )
        for force_data_url in force_data_modes:
            frame_url = await resolve_frame_image_url(
                settings,
                bot,
                photo_ref,
                force_data_url=force_data_url,
            )
            if force_data_url:
                logger.info(
                    "OpenRouter video frame inline data-url len=%s model=%s",
                    len(frame_url),
                    model_id,
                )

            body = {
                **body_base,
                "model": model_id,
                "duration": body_duration,
                "frame_images": build_frame_images(frame_url),
            }
            retry_with_data_url = False
            try_next_model = False

            for key_idx, api_key in enumerate(api_keys):
                try:
                    response = await _submit_video_job(
                        client,
                        api_key=api_key,
                        body=body,
                        submit_timeout=submit_timeout,
                    )
                except httpx.HTTPError as exc:
                    last_exc = ExternalApiError("OpenRouter", clip_error_text(exc))
                    if key_idx + 1 < len(api_keys):
                        continue
                    try_next_model = model_idx + 1 < len(model_candidates)
                    if try_next_model:
                        break
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
                    try_next_model = model_idx + 1 < len(model_candidates)
                    if try_next_model:
                        break
                    raise last_exc

                if response.status_code >= 400:
                    last_exc = _http_error_from_response(response, phase="video submit")
                    if (
                        not force_data_url
                        and _response_looks_like_image_error(
                            response.status_code,
                            response.text or "",
                        )
                    ):
                        retry_with_data_url = True
                        break
                    try_next_model = model_idx + 1 < len(model_candidates)
                    if try_next_model:
                        logger.warning(
                            "OpenRouter animate model %s failed (%s), trying %s",
                            model_id,
                            last_exc,
                            model_candidates[model_idx + 1],
                        )
                        break
                    raise last_exc

                try:
                    job = response.json()
                except ValueError as exc:
                    raise ExternalApiError("OpenRouter", "invalid JSON on video submit") from exc
                if not isinstance(job, dict):
                    raise ExternalApiError("OpenRouter", "video submit response is not an object")

                logger.info(
                    "OpenRouter video submitted model=%s job_id=%s inline=%s key=...%s",
                    model_id,
                    job.get("id"),
                    force_data_url,
                    api_key[-6:],
                )
                mp4_url = await _poll_video_job(
                    client,
                    api_key=api_key,
                    job_payload=job,
                    poll_interval_sec=poll_interval,
                    poll_timeout_sec=poll_timeout,
                )
                return OpenRouterAnimateResult(url=mp4_url, api_key=api_key)

            if retry_with_data_url:
                continue
            if try_next_model:
                break
            if last_exc is not None:
                raise last_exc
            break

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
