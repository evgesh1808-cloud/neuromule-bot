"""Пресеты налоговой ставки WB (шаг FSM перед загрузкой xlsx, интерфейс как в ЛК WB)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaxRegime = Literal["USN", "OSN", "NONE", "CUSTOM"]
UsnTaxBase = Literal["revenue", "margin"]

_DEFAULT_PRESET_ID = "USN:6.0"
_SET_TAX_PREFIX = "set_tax:"

_LEGACY_ID_MAP: dict[str, str] = {
    "usn_6": "USN:6.0",
    "usn_15": "USN:15.0",
    "none": "NONE:0.0",
}


@dataclass(frozen=True)
class AuditTaxPreset:
    id: str
    regime: TaxRegime
    label: str
    rate: float
    base: UsnTaxBase

    @property
    def rate_percent(self) -> float:
        return round(self.rate * 100.0, 2)


def _tax_base_for_regime(regime: TaxRegime, rate_percent: float) -> UsnTaxBase:
    if regime == "USN" and rate_percent > 6.0:
        return "margin"
    return "revenue"


def preset_from_regime_rate(regime: str, rate_percent: float) -> AuditTaxPreset:
    """Собирает пресет из режима и ставки в процентах (как callback ``set_tax:USN:6.0``)."""
    reg = (regime or "").strip().upper()
    pct = max(0.0, float(rate_percent))
    rate = pct / 100.0

    if reg == "NONE":
        return AuditTaxPreset("NONE:0.0", "NONE", "Не учитывать", 0.0, "revenue")
    if reg == "OSN":
        return AuditTaxPreset(
            f"OSN:{pct:g}",
            "OSN",
            f"ОСН (НДС {pct:g}%)",
            rate,
            "revenue",
        )
    if reg == "CUSTOM":
        return AuditTaxPreset(
            f"CUSTOM:{pct:g}",
            "CUSTOM",
            f"Ставка {pct:g}%",
            rate,
            _tax_base_for_regime("USN", pct),
        )
    # USN по умолчанию
    return AuditTaxPreset(
        f"USN:{pct:g}",
        "USN",
        f"УСН {pct:g}%",
        rate,
        _tax_base_for_regime("USN", pct),
    )


def parse_set_tax_callback(callback_data: str) -> AuditTaxPreset | None:
    """Парсит ``set_tax:USN:6.0`` / ``set_tax:NONE:0.0``."""
    raw = (callback_data or "").strip()
    if not raw.startswith(_SET_TAX_PREFIX):
        return None
    body = raw[len(_SET_TAX_PREFIX) :]
    parts = body.split(":")
    if len(parts) != 2:
        return None
    try:
        return preset_from_regime_rate(parts[0], float(parts[1]))
    except (TypeError, ValueError):
        return None


def default_wb_audit_tax_preset() -> AuditTaxPreset:
    return preset_from_regime_rate("USN", 6.0)


def preset_from_user_rate_percent(rate_percent: float) -> AuditTaxPreset:
    """Произвольная ставка (%) — режим CUSTOM."""
    return preset_from_regime_rate("CUSTOM", rate_percent)


def resolve_audit_tax_preset(preset_id: str | None) -> AuditTaxPreset:
    key = (preset_id or "").strip()
    if not key:
        return default_wb_audit_tax_preset()
    low = key.lower()
    if low in _LEGACY_ID_MAP:
        key = _LEGACY_ID_MAP[low]
    if key.startswith(_SET_TAX_PREFIX):
        parsed = parse_set_tax_callback(key)
        if parsed is not None:
            return parsed
    if ":" in key:
        regime, rate_s = key.split(":", 1)
        try:
            return preset_from_regime_rate(regime, float(rate_s))
        except (TypeError, ValueError):
            pass
    return default_wb_audit_tax_preset()


def compute_audit_tax_total(
    *,
    preset: AuditTaxPreset,
    tax_base_revenue: float,
    total_sku_margin: float,
) -> tuple[float, float]:
    """Возвращает (налоговая_база, tax_total)."""
    if preset.rate <= 0 or preset.regime == "NONE":
        return 0.0, 0.0
    if preset.base == "margin":
        base = max(0.0, float(total_sku_margin))
    else:
        base = max(0.0, float(tax_base_revenue))
    return round(base, 2), round(base * preset.rate, 2)


def is_custom_tax_preset(preset: AuditTaxPreset) -> bool:
    return preset.regime == "CUSTOM"


def wb_tax_selection_label(tax_type: str, tax_rate: float) -> str:
    """Подпись выбранного режима для подтверждения пользователю."""
    reg = (tax_type or "").strip().upper()
    rate = float(tax_rate)
    labels = {
        "USN": f"УСН {rate:g}%",
        "OSN": f"ОСН ({rate:g}%)" if rate != 20 else "ОСН (20%)",
        "CUSTOM": f"Своя ставка ({rate:g}%)",
        "NONE": "Не учитывать",
    }
    return labels.get(reg, "УСН 6%")


def parse_set_tax_parts(callback_data: str) -> tuple[str, float] | None:
    """``set_tax:USN:6.0`` → (``USN``, 6.0)."""
    preset = parse_set_tax_callback(callback_data)
    if preset is None:
        return None
    return preset.regime, preset.rate_percent
