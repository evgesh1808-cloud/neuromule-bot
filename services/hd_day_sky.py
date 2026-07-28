"""Локальные эфемериды дня для «Совета дня» (pyswisseph).

Считает градусы планет на полдень МСК и переводит их в человеческие
«энергетические волны» — без ворот, каналов, линий и номеров.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_MSK = timezone(timedelta(hours=3))

# 12 секторов эклиптики (по 30°) → язык чувств/состояний (не астро-жаргон).
_WAVE_THEMES: tuple[str, ...] = (
    "прилив смелости начинать новое",  # 0–30
    "волна спокойной устойчивости",  # 30–60
    "импульс любопытства и живого общения",  # 60–90
    "мягкая волна заботы о близком круге",  # 90–120
    "прилив творческого самовыражения",  # 120–150
    "фокус на деталях и чистом порядке",  # 150–180
    "волна партнёрского баланса",  # 180–210
    "глубокая волна внутренней силы",  # 210–240
    "прилив большой картины и смысла",  # 240–270
    "волна дисциплины и длинной дистанции",  # 270–300
    "импульс свободы и неожиданных идей",  # 300–330
    "тихая волна чувствительности и доверия интуиции",  # 330–360
)

_BANNED_JARGON = (
    "ворот",
    "канал",
    "транзитн",
    "эклиптик",
    "нейтрин",
    "бодиграф",
    "линия ",
    "линии ",
    "gate",
    "channel",
)


def _sector_from_longitude(longitude: float) -> int:
    return int((float(longitude) % 360.0) // 30.0) % 12


def theme_for_longitude(longitude: float) -> str:
    return _WAVE_THEMES[_sector_from_longitude(longitude)]


def day_sky_snapshot(advice_date: str) -> dict[str, float]:
    """Долготы планет на 12:00 МСК указанной даты (локально, мс)."""
    from services.hd_logic import _require_swe

    sw = _require_swe()
    d = date.fromisoformat(advice_date.strip())
    # 12:00 МСК = 09:00 UT
    jd = sw.julday(d.year, d.month, d.day, 9.0)
    bodies = {
        "sun": sw.SUN,
        "moon": sw.MOON,
        "mercury": sw.MERCURY,
        "venus": sw.VENUS,
        "mars": sw.MARS,
        "jupiter": sw.JUPITER,
        "saturn": sw.SATURN,
    }
    out: dict[str, float] = {}
    for name, planet in bodies.items():
        pos, _flags = sw.calc_ut(jd, planet)
        out[name] = round(float(pos[0]), 6)
    return out


def day_sky_prompt_blurb(advice_date: str) -> str:
    """Короткий человеческий бриф погоды дня для ночного Gemini (без жаргона)."""
    try:
        sky = day_sky_snapshot(advice_date)
    except Exception:
        logger.debug("day_sky_snapshot failed for prompt", exc_info=True)
        return (
            "Космическая погода дня мягкая и практичная. "
            "Описывай только чувства и состояния людей, без технических терминов."
        )
    sun_t = theme_for_longitude(sky["sun"])
    moon_t = theme_for_longitude(sky["moon"])
    mars_t = theme_for_longitude(sky["mars"])
    return (
        f"Фактическая погода дня (переведи в психологию, НЕ называй планеты как технику): "
        f"доминанта дня — «{sun_t}»; эмоциональный фон — «{moon_t}»; "
        f"импульс действия — «{mars_t}». "
        "Пиши только про ощущения, привычки и выборы обычного человека."
    )


def resolve_energy_wave(*, birth_raw: str, advice_date: str) -> str:
    """
    Пересечение натала с небом дня → одна «энергетическая волна» простым языком.

    Без LLM. При сбое эфемерид — безопасный дефолт.
    """
    fallback = "мягкая волна ясности и опоры на себя"
    try:
        from services.hd_logic import calculate_bodygraph_snapshot

        sky = day_sky_snapshot(advice_date)
        transit_theme = theme_for_longitude(sky["sun"])
        raw = (birth_raw or "").strip()
        if not raw:
            return transit_theme
        natal = calculate_bodygraph_snapshot(raw)
        natal_sun = natal.get("sun")
        if not isinstance(natal_sun, (int, float)):
            return transit_theme
        natal_sun_f = float(natal_sun)
        delta = abs((natal_sun_f - sky["sun"]) % 360.0)
        delta = min(delta, 360.0 - delta)
        natal_theme = theme_for_longitude(natal_sun_f)
        if delta <= 12.0:
            return f"усиленный резонанс: {natal_theme}"
        if delta <= 30.0:
            return f"живой отклик между «{natal_theme}» и «{transit_theme}»"
        return transit_theme
    except Exception:
        logger.debug("resolve_energy_wave failed", exc_info=True)
        return fallback


def strip_banned_jargon(text: str) -> str:
    """Страховка: вычищает случайный жаргон из финального текста."""
    import re

    out = text or ""
    # Технические номера вида 16-48, Gate 21 и т.п.
    out = re.sub(r"\b\d{1,2}\s*[-–—]\s*\d{1,2}\b", "", out)
    out = re.sub(r"\b(?:ворота|канал|линия)\s*\d+\b", "", out, flags=re.IGNORECASE)
    low = out.lower()
    for stem in _BANNED_JARGON:
        if stem in low:
            # мягко убираем предложения с жаргоном
            parts = re.split(r"(?<=[.!?…])\s+", out)
            kept = [p for p in parts if stem not in p.lower()]
            out = " ".join(kept) if kept else out
            low = out.lower()
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def advice_date_iso_msk(*, now: datetime | None = None) -> str:
    moment = now or datetime.now(_MSK)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_MSK)
    else:
        moment = moment.astimezone(_MSK)
    return moment.date().isoformat()
