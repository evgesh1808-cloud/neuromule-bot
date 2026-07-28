"""Эфемериды дня и «энергетическая волна» для Совета дня."""

from __future__ import annotations

from services.hd_day_sky import (
    resolve_energy_wave,
    strip_banned_jargon,
    theme_for_longitude,
)
from services.daily_advice_pool import assemble_daily_advice_from_pool


def test_theme_for_longitude_sectors() -> None:
    assert "смелост" in theme_for_longitude(10.0)
    assert "чувствительност" in theme_for_longitude(350.0)


def test_strip_banned_jargon_removes_gates_and_numbers() -> None:
    dirty = (
        "Сегодня ворота 16-48 и канал 21 дают силу. "
        "Держи фокус на деле. Транзитное Солнце усиливает линию 3."
    )
    clean = strip_banned_jargon(dirty)
    low = clean.lower()
    assert "ворот" not in low
    assert "канал" not in low
    assert "транзитн" not in low
    assert "16-48" not in clean
    assert "фокус на деле" in clean


def test_resolve_energy_wave_without_birth_is_stable() -> None:
    wave = resolve_energy_wave(birth_raw="", advice_date="2026-07-29")
    assert isinstance(wave, str) and len(wave) > 5
    assert "ворот" not in wave.lower()


def test_resolve_energy_wave_with_birth() -> None:
    wave = resolve_energy_wave(
        birth_raw="18.08.1986 12:00 Москва",
        advice_date="2026-07-29",
    )
    assert isinstance(wave, str) and len(wave) > 5
    assert "16-48" not in wave


def test_assemble_injects_energy_wave() -> None:
    row = {
        "barometer": "Мягкий день.",
        "navigator": "$display_name в $user_role ловит $energy_wave.",
        "step_plus": "шаг",
        "energy_drain": "стоп",
    }
    text = assemble_daily_advice_from_pool(
        row,
        display_name="Женя",
        user_role="по умолчанию",
        energy_wave="прилив лидерской энергии",
        cta_text="",
    )
    assert "Женя" in text
    assert "своём ритме" in text
    assert "прилив лидерской энергии" in text
    assert "по умолчанию" not in text
    assert "якорь" not in text.lower()
