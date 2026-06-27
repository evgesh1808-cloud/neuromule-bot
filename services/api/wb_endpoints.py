"""REST-эндпоинты автопилота WB API для Telegram Mini App."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_telegram_user
from services import repository as repo

router = APIRouter(prefix="/api/v1/wb", tags=["wb-autopilot"])

_WB_DAILY_CRYSTALS = 50


class WbSetupBody(BaseModel):
    api_token: str = Field(min_length=8, max_length=512)
    enabled: bool = True


class WbToggleBody(BaseModel):
    enabled: bool


@router.get("/status")
async def wb_autopilot_status(
    telegram_user_id: Annotated[int, Depends(require_telegram_user)],
) -> dict[str, Any]:
    """Текущие настройки автопилота (маска токена, тумблер)."""
    settings_row = await repo.fetch_wb_api_settings(telegram_user_id)
    if settings_row is None:
        return {
            "has_token": False,
            "token_mask": "",
            "enabled": False,
            "daily_crystals": _WB_DAILY_CRYSTALS,
        }
    return {
        **settings_row,
        "daily_crystals": _WB_DAILY_CRYSTALS,
    }


@router.post("/setup")
async def wb_autopilot_setup(
    body: WbSetupBody,
    telegram_user_id: Annotated[int, Depends(require_telegram_user)],
) -> dict[str, Any]:
    """Сохраняет WB API Key и опционально включает мониторинг."""
    from config import settings

    if not settings.wb_user_statistics_api_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Личные WB API-ключи отключены (cfo-v12). "
                "Загрузите Excel-отчёт в боте — токен не требуется."
            ),
        )
    token = body.api_token.strip()
    if len(token) < 8:
        raise HTTPException(status_code=400, detail="API token too short")
    await repo.upsert_wb_api_token(telegram_user_id, token, enabled=body.enabled)
    settings_row = await repo.fetch_wb_api_settings(telegram_user_id)
    return {
        "ok": True,
        "enabled": bool(settings_row and settings_row.get("enabled")),
        "token_mask": settings_row.get("token_mask", "") if settings_row else "",
        "daily_crystals": _WB_DAILY_CRYSTALS,
    }


@router.post("/toggle")
async def wb_autopilot_toggle(
    body: WbToggleBody,
    telegram_user_id: Annotated[int, Depends(require_telegram_user)],
) -> dict[str, Any]:
    """Мгновенно вкл/выкл ежедневный мониторинг (списание 50 💎/сутки при enabled)."""
    from config import settings

    if not settings.wb_user_statistics_api_enabled:
        raise HTTPException(
            status_code=403,
            detail="Автопилот WB API недоступен — используйте загрузку Excel без токена.",
        )
    settings_row = await repo.fetch_wb_api_settings(telegram_user_id)
    if not settings_row or not settings_row.get("has_token"):
        raise HTTPException(
            status_code=400,
            detail="Сначала сохраните WB API Key",
        )
    updated = await repo.set_wb_api_enabled(telegram_user_id, body.enabled)
    if not updated:
        raise HTTPException(status_code=404, detail="WB settings not found")
    return {
        "ok": True,
        "enabled": body.enabled,
        "daily_crystals": _WB_DAILY_CRYSTALS if body.enabled else 0,
        "billing_note": (
            f"Ежедневный мониторинг: {_WB_DAILY_CRYSTALS} 💎/сутки"
            if body.enabled
            else "Списания приостановлены"
        ),
    }
