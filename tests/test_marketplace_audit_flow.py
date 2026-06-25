"""Выбор площадки для финансового аудита в Telegram."""

from __future__ import annotations

from content import messages as msg
from platforms.marketplace_audit_flow import (
    activate_marketplace_audit,
    audit_state_for_platform,
    is_audit_file_waiting_state,
)
from platforms.telegram_keyboards import create_marketplace_audit_platform_keyboard
from platforms.telegram_states import OzonAuditingStates, WBAuditingStates
from services.file_processor import compute_seller_matrix_etl
from services.marketplace_platform import normalize_marketplace_platform, platform_display_name


def test_marketplace_audit_platform_keyboard_layout() -> None:
    kb = create_marketplace_audit_platform_keyboard()
    callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
    ]
    assert msg.TXT_AUDIT_PLATFORM_MENU
    assert f"{msg.CB_AUDIT_PLATFORM_PREFIX}wildberries" in callbacks
    assert f"{msg.CB_AUDIT_PLATFORM_PREFIX}ozon" in callbacks
    assert f"{msg.CB_AUDIT_PLATFORM_PREFIX}yandex" in callbacks
    assert f"{msg.CB_AUDIT_PLATFORM_PREFIX}1c" in callbacks
    assert msg.BTN_AUDIT_PLATFORM_WB in [btn.text for row in kb.inline_keyboard for btn in row]


def test_audit_platform_upload_instruction() -> None:
    text = msg.audit_platform_upload_instruction("ozon")
    assert "Ozon" in text
    assert ".xlsx" in text


def test_audit_state_mapping() -> None:
    assert audit_state_for_platform("wildberries") is WBAuditingStates.wait_for_xlsx
    assert audit_state_for_platform("ozon") is OzonAuditingStates.wait_for_xlsx
    assert is_audit_file_waiting_state(WBAuditingStates.wait_for_xlsx.state)


def test_platform_etl_1c_cost_column() -> None:
    matrix = [
        ["Товар", "Выручка", "Себестоимость", "Выкупили, шт.", "Доставки, шт."],
        ["SKU-A", "10000", "7000", "10", "12"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=10_000.0, platform="1c")
    assert etl is not None
    assert etl.sku_catalog
    assert etl.sku_catalog[0].net_profit < 3000


def test_normalize_marketplace_platform_aliases() -> None:
    assert normalize_marketplace_platform("wb") == "wildberries"
    assert platform_display_name("yandex") == "Яндекс.Маркет"
