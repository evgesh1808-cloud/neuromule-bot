"""Клиент Replicate API (predictions + polling). Используется воркерами видео и оживления."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

_REPLICATE_API = "https://api.replicate.com/v1"
_PLACEHOLDER_PREFIXES = ("your_", "ваш_", "your-")


def replicate_configured() -> bool:
    token = (settings.replicate_api_token or "").strip()
    if not token:
        return False
    low = token.lower()
    return not any(low.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.replicate_api_token.strip()}",
        "Content-Type": "application/json",
    }


def extract_output_url(output: Any) -> str | None:
    """Replicate output: строка URL, список URL или вложенный объект."""
    if output is None:
        return None
    if isinstance(output, str) and output.startswith(("http://", "https://")):
        return output
    if isinstance(output, list):
        for item in output:
            url = extract_output_url(item)
            if url:
                return url
    if isinstance(output, dict):
        for key in ("video", "url", "output", "result"):
            url = extract_output_url(output.get(key))
            if url:
                return url
    return None


async def call_replicate_model(
    model: str,
    inputs: dict[str, Any],
    *,
    poll_interval_sec: float | None = None,
    timeout_sec: float | None = None,
) -> str | None:
    """
    Создаёт prediction по ``model`` (owner/name) и ждёт ``succeeded``.

    Возвращает URL результата или ``None`` при ошибке / таймауте.
    """
    if not replicate_configured():
        logger.error("Replicate: REPLICATE_API_TOKEN не задан")
        return None

    model_ref = (model or "").strip()
    if not model_ref:
        logger.error("Replicate: пустой идентификатор модели")
        return None

    interval = poll_interval_sec if poll_interval_sec is not None else settings.replicate_poll_interval_sec
    timeout = timeout_sec if timeout_sec is not None else settings.replicate_poll_timeout_sec
    payload = {"model": model_ref, "input": inputs}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            create_resp = await client.post(
                f"{_REPLICATE_API}/predictions",
                json=payload,
                headers=_auth_headers(),
            )
            if create_resp.status_code not in (200, 201):
                logger.error(
                    "Replicate create %s: %s",
                    create_resp.status_code,
                    create_resp.text[:500],
                )
                return None

            prediction = create_resp.json()
            poll_url = (prediction.get("urls") or {}).get("get")
            if not poll_url:
                logger.error("Replicate: нет urls.get в ответе create")
                return None

            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                status = prediction.get("status")
                if status == "succeeded":
                    return extract_output_url(prediction.get("output"))
                if status in ("failed", "canceled"):
                    logger.error(
                        "Replicate prediction %s: %s — %s",
                        prediction.get("id"),
                        status,
                        prediction.get("error"),
                    )
                    return None
                if asyncio.get_running_loop().time() >= deadline:
                    logger.error("Replicate prediction %s: timeout", prediction.get("id"))
                    return None

                await asyncio.sleep(interval)
                poll_resp = await client.get(poll_url, headers=_auth_headers())
                if poll_resp.status_code != 200:
                    logger.error("Replicate poll %s: %s", poll_resp.status_code, poll_resp.text[:500])
                    return None
                prediction = poll_resp.json()
    except Exception:
        logger.exception("Replicate call failed model=%s", model_ref)
        return None


async def telegram_photo_download_url(bot: Any, file_id: str) -> str:
    """Публичный URL файла Telegram для передачи в Replicate (image / start_image)."""
    tg_file = await bot.get_file(file_id)
    if not tg_file or not tg_file.file_path:
        raise RuntimeError("Telegram did not return file_path for photo")
    token = (settings.tg_token or "").strip()
    if not token:
        raise RuntimeError("TG_TOKEN is not set")
    return f"https://api.telegram.org/file/bot{token}/{tg_file.file_path}"
