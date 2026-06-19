"""
Правила доступности подарочных промокодов по тарифу пользователя.

Промокоды могут быть выпущены для конкретных тарифов через CSV
``allowed_tariffs`` (например ``"SMART,ULTRA"``). На цены подписок промокоды
больше НЕ влияют — это чисто gate-механика выдачи бонусов.
"""

from __future__ import annotations

DEFAULT_ALLOWED_TARIFFS = "FREE,MINI,SMART,ULTRA"


def parse_allowed_tariffs(raw: str | None) -> set[str]:
    text = (raw or DEFAULT_ALLOWED_TARIFFS).strip()
    return {t.strip().upper() for t in text.split(",") if t.strip()}


def normalize_user_tariff(tariff: str | None) -> str:
    return (tariff or "FREE").strip().upper()


def is_tariff_allowed(user_tariff: str | None, allowed_csv: str | None) -> bool:
    return normalize_user_tariff(user_tariff) in parse_allowed_tariffs(allowed_csv)
