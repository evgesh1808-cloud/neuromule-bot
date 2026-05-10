"""Тарифы и правила доступа к инструментам."""
from __future__ import annotations

from enum import Enum

from config import Settings


class TariffName(str, Enum):
    FREE = "free"
    MINI = "mini"
    SMART = "smart"
    ULTRA = "ultra"


def normalize_tariff(raw: str | None) -> TariffName:
    value = (raw or "").strip().lower()
    if value in ("mini",):
        return TariffName.MINI
    if value in ("smart",):
        return TariffName.SMART
    if value in ("ultra",):
        return TariffName.ULTRA
    return TariffName.FREE


def can_use_music(tariff: TariffName) -> bool:
    return tariff in (TariffName.SMART, TariffName.ULTRA)


def can_use_video(tariff: TariffName) -> bool:
    return tariff is TariffName.ULTRA


def can_use_animate(tariff: TariffName) -> bool:
    return tariff is TariffName.ULTRA


def queue_priority_for_tariff(tariff: TariffName) -> int:
    if tariff is TariffName.ULTRA:
        return 1
    if tariff in (TariffName.SMART, TariffName.MINI):
        return 2
    return 3


def text_models_for_tariff(settings: Settings, tariff: TariffName) -> list[str]:
    if tariff is TariffName.FREE:
        return [settings.free_text_model]
    return list(settings.smart_models) if settings.smart_models else list(settings.free_models)
