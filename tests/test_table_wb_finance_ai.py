"""Промпт и метрики ИИ-консалтинга WB/Ozon finance."""

from __future__ import annotations

from content.chat_prompt import build_wb_marketplace_finance_system_prompt
from services.table_text_response import compute_wb_marketplace_metrics
from services.table_wb_finance_ai import (
    build_wb_finance_system_prompt_from_totals,
    build_wb_marketplace_finance_user_prompt,
    compute_wb_finance_prompt_metrics,
)


def test_build_wb_marketplace_finance_system_prompt_variables() -> None:
    prompt = build_wb_marketplace_finance_system_prompt(
        revenue="185,000.00",
        tax="11,100.00",
        clear_profit="173,900.00",
        adv_load="13.3",
        buy_ratio="72.5",
        year_forecast="2,220,000",
    )
    assert "185,000.00 руб." in prompt
    assert "11,100.00 руб." in prompt
    assert "173,900.00 руб." in prompt
    assert "реклама 13.3%" in prompt
    assert "выкуп 72.5%" in prompt
    assert "2,220,000 руб." in prompt
    assert "Senior Финансовый Директор" in prompt
    assert "БИЗНЕС-СКОРИНГ МАГАЗИНА" in prompt


def test_compute_wb_finance_prompt_metrics_from_etl() -> None:
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Удержания за продвижение",
            "К перечислению, руб.",
        ],
        ["Футболка", "8", "9", "1", "500", "4000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=6000.0)
    assert wb is not None
    metrics = compute_wb_finance_prompt_metrics(6000.0, wb)
    assert metrics is not None
    assert metrics.revenue == 6000.0
    assert metrics.tax == 360.0
    assert metrics.clear_profit == 5640.0
    assert metrics.year_forecast == 72000.0
    assert metrics.adv_load_pct == wb.ad_load_pct
    assert metrics.buy_ratio_pct == wb.buyout_coef_pct

    system = build_wb_finance_system_prompt_from_totals(6000.0, wb)
    assert system is not None
    assert "6,000.00 руб." in system
    assert f"{wb.ad_load_pct:.1f}%" in system or f"{wb.ad_load_pct:.1f}" in system

    user = build_wb_marketplace_finance_user_prompt(metrics, wb)
    assert "revenue_rub" in user
    assert "top5_unit_economics" in user
    assert "Футболка" in user
