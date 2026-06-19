"""REST-эндпоинты отчётов table_generator для Telegram Mini App / GitHub Pages."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from services import repository as repo

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/reports/{report_id}")
async def get_report_data(report_id: int) -> dict[str, Any]:
    """
    Возвращает отчёт для Telegram Mini App / GitHub Pages.

    Поле ``table_raw_json`` — распарсенный объект ``{title, headers, rows}``.
    """
    data = await repo.fetch_table_report_json(report_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "report_id": report_id,
        "table_raw_json": data,
    }
