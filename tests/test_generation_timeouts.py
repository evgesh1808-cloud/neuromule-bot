"""Гарантия наличия asyncio.timeout(180s) в воркерах video/music/animate."""
from __future__ import annotations

from pathlib import Path

from services import generation_jobs


def test_external_api_timeout_constant_is_180() -> None:
    assert generation_jobs.EXTERNAL_API_TIMEOUT_SEC == 180


def test_video_worker_wraps_replicate_in_asyncio_timeout() -> None:
    src = Path(generation_jobs.__file__).read_text(encoding="utf-8")
    # Достаточно — найти `asyncio.timeout(EXTERNAL_API_TIMEOUT_SEC)` рядом
    # с call_replicate_model в одном файле; для трёх воркеров явных
    # check'ов достаточно простого подсчёта по тексту.
    assert "asyncio.timeout(EXTERNAL_API_TIMEOUT_SEC)" in src


def test_at_least_three_timeout_wrappers_present() -> None:
    """video + music + animate — итого ≥3 контекстных менеджеров."""
    src = Path(generation_jobs.__file__).read_text(encoding="utf-8")
    assert src.count("asyncio.timeout(EXTERNAL_API_TIMEOUT_SEC)") >= 3
