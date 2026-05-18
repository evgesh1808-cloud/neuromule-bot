"""Синхронизация COST_* из .env и динамический CTA совета дня."""

from config import Settings
from services.hd_logic import get_dynamic_cta_for_today


def test_cost_defaults_match_pricelist() -> None:
    s = Settings(
        tg_token="x",
        openrouter_key="y",
        gemini_api_key="z",
    )
    assert s.cost_hd == 70
    assert s.cost_image_pro == 2
    assert s.cost_animate == 20
    assert s.cost_video == 20
    assert s.cost_music == 5


def test_dynamic_cta_uses_settings_costs() -> None:
    s = Settings(
        tg_token="x",
        openrouter_key="y",
        gemini_api_key="z",
        cost_video=20,
        cost_music=5,
        cost_animate=20,
        cost_hd=70,
        cost_match=50,
    )
    from datetime import datetime

    from services import hd_logic

    old = hd_logic._app_settings
    try:
        hd_logic._app_settings = s
        cta = get_dynamic_cta_for_today(now=datetime(2026, 5, 19))  # вторник
        assert "20 💎" in cta
        assert "видео" in cta.lower()
    finally:
        hd_logic._app_settings = old
