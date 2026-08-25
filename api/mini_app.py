"""Backend Mini App (Telegram / GitHub Pages): отчёты table_generator."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from services import repository as repo
from services.api.report_endpoints import router as reports_router
from services.api.wb_endpoints import router as wb_router
from ports.webapp_endpoints import router as webapp_router
from api.hd_endpoints import router as hd_router

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEB_DIR = _PROJECT_ROOT / "web"
_WEBAPP_DIR = _WEB_DIR / "webapp"
_TABLE_DIR = _WEB_DIR / "neuromule-table"
_HD_REPORT_DIR = _WEB_DIR / "hd-report"
_TMP_DIR = _PROJECT_ROOT / "tmp"

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
        settings.webapp_studio_url,
        settings.hd_webapp_url,
        settings.mini_app_api_base_url,
        settings.api_base_url,
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
async def _lifespan(app: FastAPI):
    await repo.init_db()
    tg_bot = None
    token = (settings.tg_token or "").strip()
    if token:
        from aiogram import Bot

        tg_bot = Bot(token=token)
        app.state.tg_bot = tg_bot
    try:
        yield
    finally:
        if tg_bot is not None:
            await tg_bot.session.close()


app = FastAPI(
    title="NeuroMule Mini App API",
    version="0.5.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Telegram-Init-Data",
        "X-VK-Launch-Params",
    ],
)

app.include_router(reports_router)
app.include_router(wb_router)
app.include_router(webapp_router)
app.include_router(hd_router)

_TMP_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/hd", StaticFiles(directory=str(_TMP_DIR)), name="hd_media")

if _WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR), html=True), name="web_hub")

if _WEBAPP_DIR.is_dir():
    app.mount("/webapp", StaticFiles(directory=str(_WEBAPP_DIR), html=True), name="webapp_studio")

if _TABLE_DIR.is_dir():
    app.mount("/neuromule-table", StaticFiles(directory=str(_TABLE_DIR), html=True), name="webapp_table")

if _HD_REPORT_DIR.is_dir():
    app.mount("/hd-report", StaticFiles(directory=str(_HD_REPORT_DIR), html=True), name="webapp_hd")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "NeuroMule",
        "hint": (
            "GET /web/ — Super App hub; GET /webapp/ — Studio; "
            "GET /neuromule-table/ — analytics; GET /hd-report/ — HD; "
            "GET /api/v1/hd/report — premium HD JSON."
        ),
    }
