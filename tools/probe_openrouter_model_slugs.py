#!/usr/bin/env python3
"""Изолированная проверка OpenRouter: :free slug vs платный slug."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings
from services.openrouter_http import (
    close_openrouter_http_client,
    init_openrouter_http_client,
)


def _safe_print(text: str) -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    data = text.encode(encoding, errors="backslashreplace")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


async def probe_model(client: httpx.AsyncClient, model: str) -> None:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Привет"}],
        "max_tokens": 32,
    }
    _safe_print(f"\n=== MODEL: {model!r} ===")
    try:
        response = await client.post(
            settings.openrouter_chat_url,
            headers=headers,
            json=payload,
            timeout=45.0,
        )
        _safe_print(f"status_code: {response.status_code}")
        try:
            body = response.json()
            _safe_print("body_json:")
            _safe_print(json.dumps(body, ensure_ascii=False, indent=2))
        except Exception:
            _safe_print("body_text:")
            _safe_print(response.text[:2000])
            body = {}
        if response.status_code == 200:
            content = (
                body.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            _safe_print(f"assistant_content: {content!r}")
    except httpx.HTTPError as exc:
        _safe_print(f"transport_error: {type(exc).__name__}: {exc}")


async def main() -> int:
    if not (settings.openrouter_key or "").strip():
        print("ERROR: OPENROUTER_API_KEY пуст в .env", file=sys.stderr)
        return 1
    _safe_print(f"endpoint: {settings.openrouter_chat_url}")
    _safe_print(f"key_set: True (len={len(settings.openrouter_key)})")
    models = (
        "google/gemini-2.5-flash:free",
        "google/gemini-2.5-flash",
    )
    client = await init_openrouter_http_client(settings)
    try:
        for model in models:
            await probe_model(client, model)
    finally:
        await close_openrouter_http_client()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
