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
        abc_a_leader_name="Футболка Premium",
        abc_a_leader_article="WRAPPER-001",
        abc_a_count="2",
        abc_c_count="1",
        abc_c_summary="DEAD (Арт: DEAD-99)",
        outsider_name="DEAD",
        outsider_article="DEAD-99",
        outsider_loss="400.00",
        outsider_buyout="0.0",
        sku_catalog_block="• Футболка Premium (Артикул: WRAPPER-001) — 100 000.00 руб. — 85 000.00 руб. — 77.8%",
        oos_forecast_line="«BOX» закончится через 3 дн. (риск OOS)",
    )
    assert "185,000.00 руб." in prompt
    assert "11,100.00 руб." in prompt
    assert "173,900.00 руб." in prompt
    assert "реклама 13.3%" in prompt or "ДРР 13.3%" in prompt
    assert "2,220,000 руб." in prompt
    assert "Senior ИИ-Аналитик" not in prompt
    assert "финансовый директор" in prompt
    assert "БИЗНЕС-СКОРИНГ МАГАЗИНА" in prompt
    assert "СВЕТОФОР ЗДОРОВЬЯ" in prompt
    assert "КАЛЬКУЛЯТОР УПУЩЕННОЙ ВЫГОДЫ" in prompt
    assert "12,500.00 руб." in prompt
    assert "2200 символов" in prompt
    assert "ABC-АНАЛИЗ" in prompt
    assert "Футболка Premium" in prompt
    assert "WRAPPER-001" in prompt
    assert "3,200.00" in prompt
    assert "КАТАЛОГ ТОВАРОВ ETL" not in prompt
    assert "Подключите" not in prompt
    assert "СЛУЖЕБНЫЕ ПРАВИЛА" in prompt
    assert "СТРАТЕГИЧЕСКИЙ ПЛАН ДЕЙСТВИЙ НА СЕГОДНЯ" in prompt
    assert "КЛЮЧЕВОЙ БИЗНЕС-ВЕРДИКТ" in prompt
    assert "ИИ-ПЛАН" not in prompt
    template_section = prompt.split("СТРУКТУРА ОТВЕТА", 1)[-1]
    assert "серверный" not in template_section.lower()
    assert "Серверный" not in template_section
    assert " ИИ" not in template_section
    assert "ИИ-" not in template_section
    assert "лидер — всего" not in prompt
    assert "всего <code>" not in prompt
    assert "ABC-АНАЛИЗ МАТРИЦЫ (локальный" not in prompt


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
    assert "sku_catalog" in user
    assert "outsider_sku" in user
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
