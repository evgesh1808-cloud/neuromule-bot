"""REST-эндпоинты отчётов table_generator для Telegram Mini App / GitHub Pages."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_telegram_user
from services import repository as repo

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/reports/{report_id}")
async def get_report_data(
    report_id: int,
    telegram_user_id: Annotated[int, Depends(require_telegram_user)],
) -> dict[str, Any]:
    """
    Возвращает отчёт для Telegram Mini App только владельцу.

    Требует валидный ``initData`` (см. :func:`api.auth.require_telegram_user`).
    Поле ``table_raw_json`` — объект ``{title, headers, rows, abc_analysis,
    out_of_stock_forecast, summary, ...}`` (WB API worker добавляет расширенные поля).
    """
    data = await repo.fetch_table_report_json_for_user(report_id, telegram_user_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "report_id": report_id,
        "table_raw_json": data,
    }
