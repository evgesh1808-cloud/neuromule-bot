"""PR-K: тесты на ``services.metrics_http`` — FastAPI-sidecar для метрик.

Используем ``httpx.AsyncClient`` + ``ASGITransport`` — это стандартный
FastAPI-паттерн для тестов без поднятия реального uvicorn / socket'а.

Покрытие:

* ``GET /health`` → 200, ``{"ok": True}``;
* ``GET /metrics/json`` → 200, актуальный ``metrics.snapshot()``;
* ``incr`` / ``observe`` отражаются в следующем запросе;
* ``reset`` обнуляет всё;
* ``serve_metrics(port=0)`` — no-op (раннее возвращение, без bind'а);
* Endpoint'ы ``/docs``, ``/redoc``, ``/openapi.json`` НЕ отвечают
  (404) — атакующая поверхность минимизирована.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from services import metrics
from services.metrics_http import build_metrics_app, serve_metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def app():
    return build_metrics_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── /health ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── /metrics/json ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_json_returns_empty_snapshot_initially(
    client: AsyncClient,
) -> None:
    resp = await client.get("/metrics/json")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload == {"counters": {}, "histograms": {}}


@pytest.mark.asyncio
async def test_metrics_json_reflects_recent_incr_and_observe(
    client: AsyncClient,
) -> None:
    metrics.incr("payment.success", {"method": "r", "pack": "MINI"})
    metrics.incr("throttle.blocked", {"kind": "callback"})
    metrics.observe("gc.phase.duration_ms", 12.5, {"gen": "0"})
    metrics.observe("gc.phase.duration_ms", 18.0, {"gen": "0"})

    resp = await client.get("/metrics/json")

    assert resp.status_code == 200
    payload = resp.json()
    assert (
        payload["counters"]["payment.success{method=r,pack=MINI}"] == 1
    )
    assert payload["counters"]["throttle.blocked{kind=callback}"] == 1
    hist = payload["histograms"]["gc.phase.duration_ms{gen=0}"]
    assert hist["count"] == 2
    assert hist["sum"] == pytest.approx(30.5)
    assert hist["min"] == pytest.approx(12.5)
    assert hist["max"] == pytest.approx(18.0)


@pytest.mark.asyncio
async def test_metrics_reset_clears_endpoint_payload(client: AsyncClient) -> None:
    metrics.incr("x")
    metrics.reset()

    resp = await client.get("/metrics/json")

    assert resp.status_code == 200
    assert resp.json() == {"counters": {}, "histograms": {}}


# ── минимизация поверхности атаки ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_endpoints_are_disabled(client: AsyncClient, path: str) -> None:
    """`/docs`, `/redoc`, `/openapi.json` — публичный API discovery,
    нам не нужны на внутреннем metrics-эндпоинте."""
    resp = await client.get(path)
    assert resp.status_code == 404


# ── serve_metrics с port<=0 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_metrics_with_zero_port_returns_immediately() -> None:
    """``METRICS_HTTP_PORT=0`` (по умолчанию) → таска no-op, без bind'а."""

    # Никакого таймаута не нужно — функция должна вернуться синхронно
    # после первой проверки port<=0.
    await serve_metrics(port=0)
    await serve_metrics(port=-1)
