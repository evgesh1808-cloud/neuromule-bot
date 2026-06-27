"""Налоговые пресеты аудита WB."""

from __future__ import annotations

import pytest

from services.audit_tax import (
    compute_audit_tax_total,
    parse_set_tax_callback,
    preset_from_regime_rate,
    preset_from_user_rate_percent,
    resolve_audit_tax_preset,
)


def test_parse_set_tax_usn_6() -> None:
    preset = parse_set_tax_callback("set_tax:USN:6.0")
    assert preset is not None
    assert preset.id == "USN:6"
    assert preset.rate == pytest.approx(0.06)
    assert preset.base == "revenue"


def test_parse_set_tax_usn_15_margin_base() -> None:
    preset = parse_set_tax_callback("set_tax:USN:15.0")
    assert preset is not None
    assert preset.base == "margin"


def test_parse_set_tax_osn_20() -> None:
    preset = parse_set_tax_callback("set_tax:OSN:20.0")
    assert preset is not None
    assert preset.regime == "OSN"
    assert preset.rate == pytest.approx(0.20)


def test_parse_set_tax_none() -> None:
    preset = parse_set_tax_callback("set_tax:NONE:0.0")
    assert preset is not None
    assert preset.rate == 0.0


def test_legacy_preset_ids() -> None:
    assert resolve_audit_tax_preset("usn_6").id == "USN:6"
    assert resolve_audit_tax_preset("usn_15").base == "margin"


def test_usn_6_tax_from_revenue() -> None:
    preset = preset_from_regime_rate("USN", 6.0)
    base, tax = compute_audit_tax_total(
        preset=preset,
        tax_base_revenue=100_000.0,
        total_sku_margin=40_000.0,
    )
    assert base == pytest.approx(100_000.0)
    assert tax == pytest.approx(6_000.0)


def test_usn_25_tax_from_margin() -> None:
    preset = preset_from_regime_rate("USN", 25.0)
    base, tax = compute_audit_tax_total(
        preset=preset,
        tax_base_revenue=100_000.0,
        total_sku_margin=40_000.0,
    )
    assert base == pytest.approx(40_000.0)
    assert tax == pytest.approx(10_000.0)


def test_custom_user_rate() -> None:
    preset = preset_from_user_rate_percent(7.5)
    assert preset.regime == "CUSTOM"
    assert preset.rate == pytest.approx(0.075)
