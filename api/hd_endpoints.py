"""REST-эндпоинты премиального HD-разбора для Telegram Mini App."""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_telegram_user
from services.hd_logic import (
    build_hd_math_data,
    compute_energy_scales_from_math,
    ensure_modern_hd_report,
    generate_premium_bodygraph,
    hd_profile_metadata,
    premium_report_from_json,
)
from services.repository import get_user_row

router = APIRouter(prefix="/api/v1", tags=["hd"])


def _api_base_url() -> str:
    from config import settings

    raw = (os.getenv("API_BASE_URL") or settings.api_base_url or settings.mini_app_api_base_url or "").strip()
    return raw.rstrip("/")


def _bodygraph_public_url(user_id: int) -> str:
    base = _api_base_url()
    if not base:
        return f"/media/hd/ready_hd_{user_id}.png"
    return f"{base}/media/hd/ready_hd_{user_id}.png"


def _ensure_bodygraph(user_id: int, birth_data: str | None, defined_centers: list[str]) -> None:
    if not birth_data or not defined_centers:
        return
    try:
        generate_premium_bodygraph(defined_centers, user_id)
    except Exception:
        pass


@router.get("/hd/report")
async def get_hd_report(
    telegram_user_id: Annotated[int, Depends(require_telegram_user)],
) -> dict[str, Any]:
    """
    Премиальный HD-разбор для Mini App.

    Требует ``Authorization: tma <initData>``.
    """
    row = await get_user_row(telegram_user_id)
    if row is None or not row.has_pro_analysis:
        raise HTTPException(status_code=404, detail="HD report not purchased")

    report, _upgraded = await ensure_modern_hd_report(telegram_user_id)
    row = await get_user_row(telegram_user_id)
    if row is None or not row.has_pro_analysis:
        raise HTTPException(status_code=404, detail="HD report not purchased")
    if report is None:
        report = premium_report_from_json(row.hd_report_json)
    if report is None:
        raise HTTPException(status_code=404, detail="HD report data invalid")

    birth_data = (row.hd_birth_data or "").strip()
    hd_type = (row.hd_type or "").strip()
    math_data = build_hd_math_data(hd_type, birth_data) if birth_data else {"hd_type": hd_type}
    meta = hd_profile_metadata(math_data)
    defined_centers = list(meta["defined_centers"])
    _ensure_bodygraph(telegram_user_id, birth_data or None, defined_centers)

    energy_scales = report.get("energy_scales")
    if not isinstance(energy_scales, dict):
        energy_scales = compute_energy_scales_from_math(math_data)

    return {
        "has_pro_analysis": True,
        "hd_type": meta["hd_type"],
        "birth_data": meta["birth_data"],
        "report": {k: report[k] for k in ("fast_facts", "money", "love", "energy", "plan") if k in report},
        "energy_scales": energy_scales,
        "bodygraph_url": _bodygraph_public_url(telegram_user_id),
        "defined_centers": defined_centers,
        "open_centers": list(meta["open_centers"]),
        "strategy": meta["strategy"],
        "authority": meta["authority"],
        "profile": meta["profile"],
    }
