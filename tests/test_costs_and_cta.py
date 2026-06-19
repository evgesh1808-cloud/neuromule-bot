"""Синхронизация COST_* из .env и динамический CTA совета дня."""

from datetime import datetime, timedelta, timezone

from config import Settings
from services.hd_logic import (
    _CTA_MONDAY_PHOTO,
    _CTA_TUESDAY_VIDEO,
    _CTA_WEEKEND_FULL_REPORT,
    get_dynamic_cta_for_today,
)

_MSK = timezone(timedelta(hours=3))


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
        cost_image_pro=3,
    )
    assert s.cost_hd == 70
    assert s.cost_image_pro == 3
    assert s.cost_animate == 20
    assert s.cost_video == 20
    assert s.cost_music == 15
    assert s.referral_bonus_energy == 5
    assert s.free_image_model == "imagen4"


def test_dynamic_cta_tuesday_uses_env_video_cost(monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_COST", "25")
    cta = get_dynamic_cta_for_today(now=datetime(2026, 5, 19, 12, 0, tzinfo=_MSK))
    assert "25 💎" in cta
    assert cta in {t.format(video=25) for t in _CTA_TUESDAY_VIDEO}


def test_dynamic_cta_monday_random_variants(monkeypatch) -> None:
    monkeypatch.setenv("PHOTO_COST", "20")
    moment = datetime(2026, 5, 18, 9, 0, tzinfo=_MSK)  # понедельник
    seen = {get_dynamic_cta_for_today(now=moment) for _ in range(40)}
    assert len(seen) >= 2
    assert all(cta in {t.format(photo=20) for t in _CTA_MONDAY_PHOTO} for cta in seen)


def test_dynamic_cta_weekend_full_report(monkeypatch) -> None:
    monkeypatch.delenv("PHOTO_COST", raising=False)
    cta = get_dynamic_cta_for_today(now=datetime(2026, 5, 22, 12, 0, tzinfo=_MSK))  # пятница
    assert cta in _CTA_WEEKEND_FULL_REPORT
    assert not cta.endswith(".")
    assert " 💎" not in cta


def test_dynamic_cta_plain_text_no_markdown(monkeypatch) -> None:
    monkeypatch.setenv("PHOTO_COST", "20")
    for _ in range(20):
        cta = get_dynamic_cta_for_today(now=datetime(2026, 5, 18, 12, 0, tzinfo=_MSK))
        assert "*" not in cta
        assert "_" not in cta
        assert "`" not in cta
        assert not cta.endswith(".")
        assert not cta.endswith("💎.")
