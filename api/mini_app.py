"""Backend Mini App (Telegram / GitHub Pages): отчёты table_generator."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from services import repository as repo
from services.api.report_endpoints import router as reports_router


def _cors_origins() -> list[str]:
    raw = (settings.mini_app_cors_origins or "").strip()
    if not raw or raw == "*":
        return ["*"]
    if raw.startswith("["):
        import json

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in raw.split(",") if part.strip()]


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await repo.init_db()
    yield


app = FastAPI(
    title="NeuroMule Mini App API",
    version="0.3.0",
    lifespan=_lifespan,
)

# CORS: GitHub Pages и любой статический фронт могут читать API без блокировки браузера.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reports_router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "NeuroMule",
        "hint": "GET /api/v1/reports/{report_id} — JSON таблицы для Mini App.",
    }
