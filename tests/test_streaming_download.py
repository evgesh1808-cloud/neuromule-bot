"""PR-J: тесты на ``services.streaming_download.stream_download_to_bytes``.

Покрытие:

* happy-path: малый файл качается полностью; метрика ``download.bytes`` пишется;
* multi-chunk: 3 чанка по 5 МБ при лимите 20 МБ → success;
* HTTP non-200 → None + ``download.http_error{status}``;
* exceeds limit (single chunk сверх лимита) → None + ``download.too_big``;
* exceeds limit при дополнении чанка → None + ``download.too_big``;
* network error (httpx.TimeoutException) → None + ``download.network_error``.

Для имитации streaming-ответа используем `httpx.MockTransport` с
кастомным handler'ом, который возвращает чанки тела.
"""
from __future__ import annotations

import pytest
import httpx

from services import metrics
from services.streaming_download import stream_download_to_bytes


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


def _make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_small_file_downloads_fully():
    payload = b"small image bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.example.com/photo.jpg",
            max_bytes=1024,
            source="vk_photo",
        )

    assert result == payload
    snap = metrics.snapshot()
    hist = snap["histograms"]["download.bytes{source=vk_photo}"]
    assert hist["count"] == 1
    assert hist["sum"] == float(len(payload))


# ── multi-chunk happy path ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_chunk_assembly_within_limit():
    """httpx сам бьёт большой response.content на чанки при iter_bytes."""
    payload = b"X" * (3 * 1024 * 1024)  # 3 МБ

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.example.com/photo.jpg",
            max_bytes=10 * 1024 * 1024,
            chunk_size=64 * 1024,
            source="vk_photo",
        )

    assert result == payload
    assert len(result) == len(payload)


# ── HTTP non-200 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_404_returns_none_and_logs_metric():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.example.com/missing.jpg",
            source="vk_photo",
        )

    assert result is None
    snap = metrics.snapshot()
    assert (
        snap["counters"]["download.http_error{source=vk_photo,status=404}"]
        == 1
    )
    assert "download.bytes{source=vk_photo}" not in snap["histograms"]


@pytest.mark.asyncio
async def test_http_503_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.example.com/photo.jpg",
            source="vk_photo",
        )

    assert result is None
    snap = metrics.snapshot()
    assert (
        snap["counters"]["download.http_error{source=vk_photo,status=503}"]
        == 1
    )


# ── exceeds limit ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_exceeds_max_bytes_returns_none():
    payload = b"Y" * (5 * 1024 * 1024)  # 5 МБ

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.example.com/big.jpg",
            max_bytes=1 * 1024 * 1024,  # 1 МБ лимит
            chunk_size=256 * 1024,
            source="vk_photo",
        )

    assert result is None
    snap = metrics.snapshot()
    assert snap["counters"]["download.too_big{source=vk_photo}"] == 1
    # Важно: bytes-гистограмма НЕ пополняется на провалах.
    assert "download.bytes{source=vk_photo}" not in snap["histograms"]


@pytest.mark.asyncio
async def test_exact_limit_boundary_is_allowed():
    """Файл ровно лимитного размера должен проходить (граница «≤»)."""
    payload = b"Z" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.example.com/edge.jpg",
            max_bytes=1024,
            source="vk_photo",
        )

    assert result == payload


# ── network errors ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_returns_none_and_logs_metric():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("read timed out", request=request)

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.example.com/slow.jpg",
            source="vk_photo",
        )

    assert result is None
    snap = metrics.snapshot()
    assert snap["counters"]["download.network_error{source=vk_photo}"] == 1


@pytest.mark.asyncio
async def test_connect_error_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns lookup failed", request=request)

    async with _make_client(handler) as client:
        result = await stream_download_to_bytes(
            client,
            "https://cdn.unreachable.example.com/img.jpg",
            source="vk_photo",
        )

    assert result is None
    snap = metrics.snapshot()
    assert snap["counters"]["download.network_error{source=vk_photo}"] == 1


# ── source label propagation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_source_label_is_carried_into_metrics():
    """Тот же тип ошибки с разным source — два разных счётчика."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _make_client(handler) as client:
        await stream_download_to_bytes(client, "https://x/1", source="vk_photo")
        await stream_download_to_bytes(client, "https://x/2", source="max_app_video")

    snap = metrics.snapshot()
    assert (
        snap["counters"]["download.http_error{source=vk_photo,status=404}"] == 1
    )
    assert (
        snap["counters"][
            "download.http_error{source=max_app_video,status=404}"
        ]
        == 1
    )
