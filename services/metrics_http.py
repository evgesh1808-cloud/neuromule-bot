"""Опциональный HTTP-эндпоинт для дампа ``metrics.snapshot()``.

Sidecar-паттерн: лёгкий FastAPI запускается в том же процессе, что и
aiogram-Dispatcher, чтобы snapshot был «живым» (один in-process сторадж).
По умолчанию НЕ слушает — поднимается только если в конфиге задан
``metrics_http_port > 0``. Bind ровно на ``127.0.0.1`` — никакой
экспозиции наружу без явного reverse-proxy.

Endpoints:

* ``GET /health`` → ``{"ok": True}`` — для liveness-probe;
* ``GET /metrics/json`` → структура из ``metrics.snapshot()`` (JSON-сериализуема).

Когда понадобится Prometheus text format — добавим ``/metrics``-роут
рядом, форматтер `_to_prometheus_text(snapshot)` — отдельной функцией.
Call-sites (``incr`` / ``observe``) трогать не нужно.

Безопасность:

* generic ``except Exception`` отсутствует;
* shutdown через `asyncio.CancelledError` — server.should_exit=True;
* ни одного фонового таска без cancellation-path;
* никаких записей в файлы / БД — только in-memory snapshot.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import metrics

logger = logging.getLogger(__name__)


def build_metrics_app() -> Any:
    """Создать FastAPI-приложение с endpoint'ами для observability.

    Endpoints:

    * ``GET /health`` → ``{"ok": True}`` для liveness-probe;
    * ``GET /metrics/json`` → snapshot в JSON (для дашборда WebApp);
    * ``GET /metrics`` → Prometheus exposition format 0.0.4 (для
      vmagent / vmscraper / Prometheus scraper).

    Лениво импортирует ``FastAPI`` — это даёт совместимость с тестами,
    которые могут переопределять зависимость до импорта модуля.
    """
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    from services.metrics_prometheus import to_prometheus_text

    app = FastAPI(
        title="NeuroMule Metrics",
        version="1.0.0",
        docs_url=None,  # никаких /docs наружу — лишняя поверхность
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/metrics/json")
    def metrics_json() -> dict[str, Any]:
        return metrics.snapshot()

    @app.get(
        "/metrics",
        response_class=PlainTextResponse,
        responses={200: {"content": {"text/plain": {}}}},
    )
    def metrics_prom() -> str:
        return to_prometheus_text(metrics.snapshot())

    @app.post("/webhooks/yookassa")
    async def yookassa_webhook(request: Any) -> dict[str, str]:
        from services.billing import shop as payment_shop
        from services.billing.shop import PaymentOutcome

        body = await request.json()
        result = await payment_shop.handle_yookassa_webhook(body)
        if result.outcome is PaymentOutcome.SUCCESS:
            return {"status": "ok"}
        if result.outcome is PaymentOutcome.DUPLICATE:
            return {"status": "duplicate"}
        if result.outcome is PaymentOutcome.IGNORED:
            return {"status": "ignored"}
        return {"status": "invalid"}

    return app


async def serve_metrics(
    *,
    host: str = "127.0.0.1",
    port: int,
    log_level: str = "warning",
) -> None:
    """Запускает uvicorn-сервер с метриками.

    Корректно обрабатывает ``CancelledError`` — выставляет
    ``server.should_exit = True`` и пробрасывает отмену, чтобы фоновая
    task'а в ``run_telegram`` могла finalize-нуться без зомби-сокетов.
    """
    import uvicorn

    if int(port) <= 0:
        logger.info("metrics_http: port<=0, endpoint disabled")
        return

    config = uvicorn.Config(
        build_metrics_app(),
        host=host,
        port=int(port),
        log_level=log_level,
        access_log=False,
        lifespan="off",  # без startup/shutdown hook'ов — у нас нет state
    )
    server = uvicorn.Server(config)
    logger.info(
        "metrics_http: serving on http://%s:%s/metrics/json", host, int(port)
    )
    try:
        await server.serve()
    except asyncio.CancelledError:
        logger.info("metrics_http: shutdown requested")
        server.should_exit = True
        raise


__all__ = ("build_metrics_app", "serve_metrics")
