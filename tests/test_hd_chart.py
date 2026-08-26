"""Tests for services/hd_chart.py — IHDS math core."""
from __future__ import annotations

import pytest

from services import hd_chart, hd_logic


@pytest.mark.skipif(hd_chart.swe is None, reason="pyswisseph not installed")
def test_build_pure_hd_chart_cheboksary_profile_not_5_5() -> None:
    chart = hd_chart.build_pure_hd_chart("18.08.1986 03:40 Чебоксары")
    assert chart["hd_type"] == "Генератор"
    assert chart["profile"] == "5/1"
    assert chart["profile_archetype"] == "Спасатель-Исследователь"
    assert "10-34" in chart["active_channels"]
    assert "28-38" in chart["active_channels"]
    assert "5-15" in chart["active_channels"]
    assert chart["timezone"] == "Europe/Moscow"


@pytest.mark.skipif(hd_chart.swe is None, reason="pyswisseph not installed")
def test_build_hd_math_data_uses_full_personality_and_design() -> None:
    math_data = hd_logic.build_hd_math_data("не указан", "18.08.1986 03:40 Чебоксары")
    assert math_data.get("profile") == "5/1"
    assert "5-15" in (math_data.get("active_channels") or [])
    domain_pairs = math_data.get("domain_synthesis_pairs")
    assert isinstance(domain_pairs, dict)
    assert set(domain_pairs.keys()) == {"money", "love", "energy"}


def test_sanitize_preserves_plan_day_ranges() -> None:
    raw = "Дни 1-5 — дерзкий вызов. Канал 10-34 даёт силу. 15-20 минут отдыха."
    cleaned = hd_logic._sanitize_hd_user_facing_text(
        raw,
        active_channels=["10-34"],
    )
    assert "1-5" in cleaned
    assert "15-20 минут" in cleaned
    assert "10-34" not in cleaned
    assert "Суперсила" in cleaned or "влияни" in cleaned.lower()


def test_sanitize_does_not_replace_unknown_channel_codes() -> None:
    raw = "В плане используй канал 99-99 как метафору."
    cleaned = hd_logic._sanitize_hd_user_facing_text(raw, active_channels=["10-34"])
    assert "99-99" in cleaned
