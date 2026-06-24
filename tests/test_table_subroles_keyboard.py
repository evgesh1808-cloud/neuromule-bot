"""Под-режимы table_generator: клавиатура и FSM callback."""

from __future__ import annotations

from platforms.telegram_keyboards import create_table_subroles_keyboard
from content import messages as msg
from services.table_processing_worker import table_jobs_semaphore


def test_create_table_subroles_keyboard_layout() -> None:
    kb = create_table_subroles_keyboard()
    assert len(kb.inline_keyboard) == 3
    assert len(kb.inline_keyboard[0]) == 2
    assert len(kb.inline_keyboard[1]) == 2
    labels = [btn.text for row in kb.inline_keyboard[:2] for btn in row]
    assert "📊 Базовый отчёт" in labels
    assert "💼 Финансы WB/Ozon" in labels
    assert "📈 Маркетинг ROI" in labels
    assert "📝 SEO (Excel)" in labels
    callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
    ]
    assert f"{msg.CB_TABLE_SUBROLE_PREFIX}standard_report" in callbacks
    assert f"{msg.CB_TABLE_SUBROLE_PREFIX}wb_ozon_finance" in callbacks
    assert f"{msg.CB_TABLE_SUBROLE_PREFIX}traffic_marketing" in callbacks
    assert f"{msg.CB_TABLE_SUBROLE_PREFIX}mass_seo_generation" in callbacks
    assert msg.CB_BACK_TO_ROLES_MENU in callbacks


def test_table_jobs_semaphore_single_slot() -> None:
    assert table_jobs_semaphore._value == 1  # noqa: SLF001 — контракт 1 ядро CPU


def test_table_subrole_instructions() -> None:
    assert "за 0 рублей" in msg.table_subrole_instruction("wb_ozon_finance")
    assert "ROI" in msg.table_subrole_instruction("traffic_marketing")
    assert "SEO-описания" in msg.table_subrole_instruction("mass_seo_generation")
    assert "классический технический анализ" in msg.table_subrole_instruction("standard_report")
