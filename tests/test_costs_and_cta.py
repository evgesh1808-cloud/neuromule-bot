"""Синхронизация COST_* из .env и динамический CTA совета дня."""

from config import Settings
from services.hd_logic import get_dynamic_cta_for_today


def test_cost_defaults_match_pricelist(monkeypatch) -> None:
    """Дефолты из config.py (без переопределения из локального .env)."""
    for key in (
        "COST_HD",
        "COST_IMAGE_PRO",
        "COST_ANIMATE",
        "COST_VIDEO",
        "COST_MUSIC",
        "REFERRAL_BONUS_ENERGY",
        "FREE_IMAGE_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    s = Settings(
        tg_token="x",
        openrouter_key="y",
        gemini_api_key="z",
    )
    assert s.cost_hd == 70
    assert s.cost_image_pro == 2
    assert s.cost_animate == 20
    assert s.cost_video == 20
    assert s.cost_music == 15
    assert s.referral_bonus_energy == 5
    assert s.free_image_model == "imagen4"


def test_dynamic_cta_uses_settings_costs() -> None:
    s = Settings(
        tg_token="x",
        openrouter_key="y",
        gemini_api_key="z",
        cost_video=20,
        cost_music=15,
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
