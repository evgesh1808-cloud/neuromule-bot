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
    platform: str = "wildberries",
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
    platform_key = (platform or "wildberries").strip().lower()
    allowed = {"wildberries", "wb", "ozon", "yandex", "yandex_market", "1c", "moysklad"}
    if platform_key not in allowed:
        platform_key = "wildberries"
    if platform_key == "wb":
        platform_key = "wildberries"
    return {
        "report_id": report_id,
        "platform": platform_key,
        "table_raw_json": data,
    }
