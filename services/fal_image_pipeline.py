"""fal.ai: двухэтапный i2i (flux/schnell → face-swap) и upscale по URL."""

from __future__ import annotations

import logging
import os
from typing import Any

from config import settings as app_settings
from services.api_resilience import ExternalApiError

logger = logging.getLogger(__name__)

FAL_FLUX_SCHNELL = "fal-ai/flux/schnell"
FAL_FACE_SWAP = "fal-ai/fash-cron/face-swap"
FAL_CREATIVE_UPSCALER = "fal-ai/creative-upscaler"


def fal_configured() -> bool:
    return bool(_fal_key())


def _fal_key() -> str:
    return (app_settings.fal_api_key or os.environ.get("FAL_KEY") or "").strip()


def _ensure_fal_key() -> str:
    key = _fal_key()
    if not key:
        raise ExternalApiError("fal.ai", "FAL_KEY не задан")
    os.environ.setdefault("FAL_KEY", key)
    return key


def _extract_image_url(result: dict[str, Any]) -> str:
    images = result.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            url = (first.get("url") or "").strip()
            if url:
                return url
        elif isinstance(first, str) and first.strip():
            return first.strip()

    for key in ("image", "output"):
        block = result.get(key)
        if isinstance(block, dict):
            url = (block.get("url") or "").strip()
            if url:
                return url

    url = (result.get("url") or "").strip()
    if url:
        return url

    raise ExternalApiError("fal.ai", "ответ без URL изображения")


async def upload_fal_image_bytes(data: bytes, content_type: str = "image/jpeg") -> str:
    raw = data if isinstance(data, bytes) else bytes(data)
    if not raw:
        raise ExternalApiError("fal.ai", "пустые байты изображения")
    mime = (content_type or "image/jpeg").strip() or "image/jpeg"
    _ensure_fal_key()
    try:
        import fal_client
    except ImportError as exc:
        raise ExternalApiError("fal.ai", "pip install fal-client") from exc
    try:
        upload_async = getattr(fal_client, "upload_async", None)
        if callable(upload_async):
            return str(await upload_async(raw, mime))
        return str(fal_client.upload(raw, mime))
    except ExternalApiError:
        raise
    except Exception as exc:
        raise ExternalApiError("fal.ai", str(exc)) from exc


async def fal_subscribe(model: str, arguments: dict[str, Any]) -> dict[str, Any]:
    _ensure_fal_key()
    try:
        import fal_client
    except ImportError as exc:
        raise ExternalApiError("fal.ai", "pip install fal-client") from exc

    try:
        return await fal_client.subscribe_async(model, arguments=arguments)
    except ExternalApiError:
        raise
    except Exception as exc:
        raise ExternalApiError("fal.ai", str(exc)) from exc


async def generate_fal_i2i_reference(
    prompt: str,
    swap_image_url: str,
    *,
    seed: int | None = None,
) -> str:
    text = (prompt or "").strip() or "portrait photo"
    swap_url = (swap_image_url or "").strip()
    if not swap_url:
        raise ExternalApiError("fal.ai", "swap_image_url пуст")

    args_a: dict[str, Any] = {
        "prompt": text,
        "image_size": "square_hd",
        "sync_mode": True,
    }
    if seed is not None:
        args_a["seed"] = int(seed)

    logger.info("fal i2i step A flux/schnell seed=%s", seed)
    base_url = _extract_image_url(await fal_subscribe(FAL_FLUX_SCHNELL, args_a))

    logger.info("fal i2i step B face-swap")
    final_url = _extract_image_url(
        await fal_subscribe(
            FAL_FACE_SWAP,
            {
                "base_image_url": base_url,
                "swap_image_url": swap_url,
            },
        )
    )
    return final_url


async def upscale_fal_image(image_url: str, *, scale_value: int) -> str:
    src = (image_url or "").strip()
    if not src:
        raise ExternalApiError("fal.ai", "image_url пуст")
    scale = int(scale_value)
    if scale not in (2, 4):
        raise ExternalApiError("fal.ai", f"unsupported scale_value={scale}")

    return _extract_image_url(
        await fal_subscribe(
            FAL_CREATIVE_UPSCALER,
            {"image_url": src, "scale_value": scale},
        )
    )
