"""Легковесный FastAPI для Mini App / Web-кнопки «Саммари»."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from config import settings
from core.summarizer import SummarizeResult, summarize_from_user_input, summarize_text


class SummarizeTextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50_000)


class SummarizeResponse(BaseModel):
    ok: bool
    summary: str = ""
    error: str = ""


def _check_api_key(x_api_key: str | None) -> None:
    expected = settings.summarizer_api_key.strip()
    if not expected:
        return
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def _to_response(result: SummarizeResult) -> SummarizeResponse:
    if result.ok:
        return SummarizeResponse(ok=True, summary=result.summary)
    return SummarizeResponse(ok=False, error=result.error_message or "Ошибка саммари")


def build_summarizer_app() -> FastAPI:
    app = FastAPI(
        title="NeuroMule Summarizer API",
        version="1.0.0",
        docs_url="/docs" if settings.summarizer_api_docs else None,
        redoc_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/v1/summarize", response_model=SummarizeResponse)
    async def summarize_endpoint(
        body: SummarizeTextRequest,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> SummarizeResponse:
        _check_api_key(x_api_key)
        result = await summarize_from_user_input(body.text)
        return _to_response(result)

    @app.post("/api/v1/summarize/raw", response_model=SummarizeResponse)
    async def summarize_raw_endpoint(
        body: SummarizeTextRequest,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> SummarizeResponse:
        """Без разбора URL — только готовый текст (для Mini App после клиентского парсинга)."""
        _check_api_key(x_api_key)
        result = await summarize_text(body.text)
        return _to_response(result)

    return app


app: Any = build_summarizer_app()
