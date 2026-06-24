"""Backend Mini App (Telegram / GitHub Pages): отчёты table_generator."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from services import repository as repo
from services.api.report_endpoints import router as reports_router
from services.api.wb_endpoints import router as wb_router

logger = logging.getLogger(__name__)

# Дефолтные origin для GitHub Pages / собственного фронта таблиц.
_DEFAULT_TABLE_REPORTS_ORIGIN = "https://your-user.github.io"


def _origin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_origins_raw(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


def _fallback_webapp_origins() -> list[str]:
    """Явные origin из URL WebApp в конфиге (без wildcard)."""
    candidates = (
        settings.webapp_table_reports_url,
        settings.webapp_shop_url,
        settings.webapp_gallery_url,
    )
    origins: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        origin = _origin_from_url(url)
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    if not origins:
        origins.append(_DEFAULT_TABLE_REPORTS_ORIGIN)
    return origins


def _cors_origins() -> list[str]:
    """
    CORS origins для Mini App API.

    При ``allow_credentials=True`` wildcard ``*`` запрещён спецификацией CORS.
    Если в конфиге ``*`` или пусто — используем явный список из WebApp URL.
    """
    configured = _parse_origins_raw(settings.mini_app_cors_origins or "")
    if configured == ["*"] or not configured:
        origins = _fallback_webapp_origins()
        if configured == ["*"]:
            logger.warning(
                "MINI_APP_CORS_ORIGINS=* ignored with allow_credentials=True; "
                "using explicit origins: %s",
                origins,
            )
        return origins
    return configured


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await repo.init_db()
    yield


app = FastAPI(
    title="NeuroMule Mini App API",
    version="0.4.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Telegram-Init-Data"],
)

app.include_router(reports_router)
app.include_router(wb_router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "NeuroMule",
        "hint": "GET /api/v1/reports/{report_id} — JSON таблицы (требуется Telegram initData).",
    }
