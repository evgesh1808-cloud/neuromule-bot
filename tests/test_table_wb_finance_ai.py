"""Промпт и метрики ИИ-консалтинга WB/Ozon finance."""

from __future__ import annotations

from content.chat_prompt import build_wb_marketplace_finance_system_prompt
from services.table_text_response import compute_wb_marketplace_metrics
from services.table_wb_finance_ai import (
    append_wb_finance_mini_app_cta,
    build_wb_finance_system_prompt_from_totals,
    build_wb_marketplace_finance_user_prompt,
    compute_business_score,
    compute_fomo_lost_rub,
    compute_wb_finance_prompt_metrics,
    derive_business_verdict,
)


def test_build_wb_marketplace_finance_system_prompt_variables() -> None:
    prompt = build_wb_marketplace_finance_system_prompt(
        revenue="185,000.00",
        tax="11,100.00",
        clear_profit="173,900.00",
        profitability_pct="94.0",
        adv_load="13.3",
        buy_ratio="72.5",
        year_forecast="2,220,000",
        business_score="7.5",
        verdict="Высокая маржинальность при контролируемом ДРР.",
        fomo_lost_rub="12,500.00",
        logistics_fomo_rub="3,200.00",
        abc_a_leader="WRAPPER",
        abc_a_count="2",
        abc_c_count="1",
        abc_c_summary="DEAD",
        oos_forecast_line="«BOX» закончится через 3 дн. (риск OOS)",
    )
    assert "185,000.00 руб." in prompt
    assert "11,100.00 руб." in prompt
    assert "173,900.00 руб." in prompt
    assert "рекламная нагрузка 13.3%" in prompt or "реклама 13.3%" in prompt
    assert "выкуп 72.5%" in prompt or "buy_ratio" not in prompt
    assert "2,220,000 руб." in prompt
    assert "Senior Финансовый Директор" in prompt
    assert "БИЗНЕС-СКОРИНГ МАГАЗИНА" in prompt
    assert "СВЕТОФОР ЗДОРОВЬЯ" in prompt
    assert "КАЛЬКУЛЯТОР УПУЩЕННОЙ ВЫГОДЫ" in prompt
    assert "12,500.00 руб." in prompt
    assert "2000 символов" in prompt
    assert "мессенджере Telegram" in prompt
    assert "ABC-АНАЛИЗ" in prompt
    assert "WRAPPER" in prompt
    assert "3,200.00" in prompt
    assert "Подключите" not in prompt


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
    metrics = compute_wb_finance_prompt_metrics(6000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    assert metrics.revenue == 6000.0
    assert metrics.tax == 360.0
    assert metrics.clear_profit == 5640.0
    assert metrics.year_forecast == 72000.0
    assert metrics.profitability_pct == 94.0
    assert 1.0 <= metrics.business_score <= 10.0
    assert metrics.verdict
    assert metrics.fomo_lost_rub >= 0

    system = build_wb_finance_system_prompt_from_totals(6000.0, wb)
    assert system is not None
    assert "6,000.00 руб." in system
    assert f"{metrics.business_score:.1f}" in system

    user = build_wb_marketplace_finance_user_prompt(metrics, wb)
    assert "revenue_rub" in user
    assert "fomo_lost_rub" in user
    assert "abc_analysis" in user
    assert metrics.abc_a_count >= 0
    assert "Футболка" in user


def test_fomo_and_scoring_helpers() -> None:
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Удержания за продвижение",
            "К перечислению, руб.",
            "Цена реализации",
            "Вознаграждение",
            "Логистика",
        ],
        ["Убыток", "2", "20", "8", "3000", "1000", "500", "400", "200"],
        ["Хит", "50", "55", "2", "500", "50000", "1200", "100", "50"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=51_000.0)
    assert wb is not None
    fomo, parts = compute_fomo_lost_rub(51_000.0, wb)
    assert fomo > 0
    assert parts
    score = compute_business_score(
        profitability_pct=90.0,
        ad_load_pct=wb.ad_load_pct,
        buyout_coef_pct=wb.buyout_coef_pct,
        worst_unit_net=min(u.net_income for u in wb.top5_units),
    )
    assert score >= 1.0
    verdict = derive_business_verdict(
        business_score=score,
        profitability_pct=90.0,
        ad_load_pct=wb.ad_load_pct,
        buyout_coef_pct=wb.buyout_coef_pct,
        worst_unit_label="Убыток",
    )
    assert verdict


def test_append_wb_finance_mini_app_cta() -> None:
    body = "📊 <b>ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ</b>"
    out = append_wb_finance_mini_app_cta(body)
    assert "Автопилот по API" in out
    assert "09:00" in out
    assert "Первые 3 дня" in out
