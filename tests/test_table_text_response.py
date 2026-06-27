"""Шаблон «Один экран» для table_generator: без дублей таблиц и формул."""

from __future__ import annotations

import pytest

from services.table_generator_pack import build_table_generator_pack
from services.table_json import parse_table_json_response
from services.table_text_response import (
    build_table_one_screen_html,
    build_wb_finance_express_html,
    extract_table_ai_insights,
    fmt_money,
    strip_ascii_tables,
    strip_math_formulas,
)

MONTHLY_JSON = (
    '{"title":"Выручка","headers":["Месяц","Выручка"],'
    '"rows":[["Январь","60000"],["Февраль","55000"],["Март","70000"]]}'
)


def test_fmt_money_thousands() -> None:
    assert fmt_money(60000) == "60,000 руб."
    assert fmt_money(1_250_500.5) == "1,250,500.50 руб."


def test_strip_ascii_tables() -> None:
    raw = (
        "Анализ продаж\n"
        "+---+---+\n"
        "| Янв | 60k |\n"
        "+---+---+\n"
        "Рост в марте."
    )
    assert strip_ascii_tables(raw) == "Анализ продаж\nРост в марте."


def test_strip_math_formulas() -> None:
    raw = (
        "Тренд положительный.\n"
        "60000 + 55000 = 115000\n"
        "Рекомендуем усилить маркетинг."
    )
    out = strip_math_formulas(raw)
    assert "60000" not in out
    assert "Рекомендуем" in out


def test_extract_table_ai_insights_from_mixed_response() -> None:
    blob = (
        "Краткий вывод: спад в феврале.\n"
        f"{MONTHLY_JSON}\n"
        "| Месяц | Сумма |\n"
        "60000 + 55000 = 115000"
    )
    insights = extract_table_ai_insights(blob)
    assert "спад в феврале" in insights
    assert "{" not in insights
    assert "|" not in insights
    assert "60000" not in insights


def test_build_wb_finance_express_html() -> None:
    html = build_wb_finance_express_html(185_000.0)
    assert "ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ" in html
    assert "185,000.00" in html
    assert "11,100.00" in html  # tax 6%
    assert "173,900.00" in html  # net
    assert "РАСХОДЫ НА ХРАНЕНИЕ" in html
    assert "СИСТЕМНЫЕ УДЕРЖАНИЯ" in html
    assert "ПРОГНОЗ И ОБНУЛЕНИЕ ОСТАТКОВ" not in html
    assert "Fact-Based Audit Build" in html
    assert "НАЛОГ УСН" in html
    assert "ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД" in html
    assert "ОБЩАЯ ВЫРУЧКА" in html
    assert "ПЛАН ДЕЙСТВИЙ ДЛЯ ПРЕДПРИНИМАТЕЛЯ" not in html
    assert "СТРАТЕГИЧЕСКИЕ РЕКОМЕНДАЦИИ CFO" in html
    assert "ОПЕРАЦИОННЫЙ АУДИТ ПОСТАВОК" in html
    assert "СВЕТОФОР" in html
    assert "Серверный" not in html
    assert "ИИ-ПЛАН" not in html
    assert "интерактивный дашборд" not in html


def test_finance_block_shows_storage_and_system_losses() -> None:
    from services.table_wb_finance_ai import (
        WbFinancePromptMetrics,
        build_wb_finance_express_html_local,
    )

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=20_000.0,
        adv_load_pct=12.0,
        buy_ratio_pct=70.0,
        year_forecast=1_200_000.0,
        profitability_pct=20.0,
        business_score=8.0,
        verdict="Высокая маржинальность — фокус на масштабировании лидеров ассортимента.",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        storage_cost=2_500.0,
        total_system_losses=500.0,
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "2,500.00" in html
    assert "500.00" in html
    assert "Спасение Cash Flow" in html or "Контроль Cash Flow" in html


def test_supply_chain_audit_block_in_report() -> None:
    from services.table_wb_finance_ai import WbFinancePromptMetrics, build_wb_finance_express_html_local

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=20_000.0,
        adv_load_pct=12.0,
        buy_ratio_pct=70.0,
        year_forecast=1_200_000.0,
        profitability_pct=20.0,
        business_score=8.0,
        verdict="Высокая маржинальность — фокус на масштабировании лидеров ассортимента.",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        top_regions=("Карелия", "Краснодар", "Алтай"),
        top_warehouses=("Рязань", "Тула"),
        canceled_skus=("SKU-C", "SKU-D"),
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "ОПЕРАЦИОННЫЙ АУДИТ ПОСТАВОК" in html
    assert "Рязань" in html
    assert "Карелия" in html
    assert "SKU-C" in html
    assert "отмены заказов" in html


def test_cost_structure_block_with_losses() -> None:
    from services.table_wb_finance_ai import WbFinancePromptMetrics, build_wb_finance_express_html_local

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=20_000.0,
        adv_load_pct=12.0,
        buy_ratio_pct=70.0,
        year_forecast=1_200_000.0,
        profitability_pct=20.0,
        business_score=8.0,
        verdict="Высокая маржинальность — фокус на масштабировании лидеров ассортимента.",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        storage_cost=2_500.0,
        total_system_losses=500.0,
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "СТРУКТУРА ИЗДЕРЖЕК И КОММЕРЧЕСКИХ УДЕРЖАНИЙ" in html
    assert "УДЕРЖАНИЯ ПО КРЕДИТАМ / ШТРАФАМ" in html
    assert "500.00" in html
    assert "ПЛАТНОЕ ХРАНЕНИЕ НА СКЛАДАХ FBO" in html
    assert "2,500.00" in html
    assert "ИТОГОВЫЙ ОПЕРАЦИОННЫЙ УБЫТОК" in html
    assert "20,000.00" in html
    assert "КОМИССИИ И АКЦИИ WB" in html
    assert "30-40%" in html
    assert "КРИТИЧЕСКАЯ ЗОНА" in html
    assert "500.00" in html


def test_cost_structure_block_praise_when_zero_costs() -> None:
    from services.table_wb_finance_ai import WbFinancePromptMetrics, build_wb_finance_express_html_local

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=20_000.0,
        adv_load_pct=12.0,
        buy_ratio_pct=70.0,
        year_forecast=1_200_000.0,
        profitability_pct=20.0,
        business_score=8.0,
        verdict="Высокая маржинальность — фокус на масштабировании лидеров ассортимента.",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
    )
    html = build_wb_finance_express_html_local(metrics, None)
    assert "СТРУКТУРА ИЗДЕРЖЕК И КОММЕРЧЕСКИХ УДЕРЖАНИЙ" in html
    assert "УДЕРЖАНИЯ ПО КРЕДИТАМ / ШТРАФАМ" in html
    assert "0.00" in html
    assert "ПЛАТНОЕ ХРАНЕНИЕ НА СКЛАДАХ FBO" in html
    assert "ИТОГОВЫЙ ОПЕРАЦИОННЫЙ УБЫТОК" in html
    assert "КОМИССИИ И АКЦИИ WB" in html
    assert "Эффективность юнит-экономики 100%" not in html
    assert "внереализационные списания" not in html


def test_main_verdict_system_losses_override() -> None:
    from services.table_text_response import _resolve_main_analytical_verdict
    from services.table_wb_finance_ai import WbFinancePromptMetrics

    metrics = WbFinancePromptMetrics(
        revenue=100_000.0,
        tax=6_000.0,
        clear_profit=-1_500.0,
        adv_load_pct=12.0,
        buy_ratio_pct=70.0,
        year_forecast=1_200_000.0,
        profitability_pct=-1.5,
        business_score=4.0,
        verdict="Высокая маржинальность — фокус на масштабировании лидеров ассортимента.",
        fomo_lost_rub=0.0,
        fomo_breakdown=(),
        total_system_losses=3_000.0,
        operational_profit=1_500.0,
    )
    verdict = _resolve_main_analytical_verdict(metrics, None)
    assert "системными удержаниями" in verdict
    assert "масштабировании лидеров" not in verdict


def test_compute_wb_marketplace_metrics_local_features() -> None:
    from services.table_text_response import compute_wb_marketplace_metrics

    matrix = [
        [
            "Предмет",
            "Заказано, шт.",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Удержания за продвижение",
            "К перечислению, руб.",
        ],
        ["Футболка", "10", "8", "9", "1", "500", "4000"],
        ["Шорты", "5", "4", "4", "1", "300", "2000"],
    ]
    metrics = compute_wb_marketplace_metrics(matrix, revenue_total=6000.0)
    assert metrics is not None
    assert metrics.total_advertising_cost == 800.0
    assert abs(metrics.ad_load_pct - 800 / 6000 * 100) < 0.01
    assert metrics.sales_qty == 12.0
    assert metrics.buyout_coef_pct == pytest.approx(12 / (12 + 2) * 100, rel=1e-3)
    assert len(metrics.top5_units) == 2
    assert metrics.top5_units[0].label == "Футболка"
    assert any("Рекламная нагрузка" in line for line in metrics.insight_lines)
    assert any("выкупа" in line.lower() for line in metrics.insight_lines)


def test_build_table_one_screen_html_wb_finance_subrole() -> None:
    payload = parse_table_json_response(MONTHLY_JSON)
    assert payload is not None
    html = build_table_one_screen_html(
        payload,
        table_subrole="wb_ozon_finance",
    )
    assert "ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ" in html
    assert "185,000.00" in html
    assert "Январь" not in html


def test_build_table_generator_pack_wb_finance_subrole() -> None:
    pack = build_table_generator_pack(
        MONTHLY_JSON,
        ai_insights="Игнорируется для finance.",
        table_subrole="wb_ozon_finance",
    )
    assert pack is not None
    assert "ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ" in pack.telegram_caption_html
    assert "Аналитическое заключение" not in pack.telegram_caption_html


def test_build_table_one_screen_html_local_totals() -> None:
    payload = parse_table_json_response(MONTHLY_JSON)
    assert payload is not None
    html = build_table_one_screen_html(
        payload,
        ai_insights="Выручка растёт с января по март; пик — март.",
    )
    assert "ИТОГО" in html
    assert "185,000" in html
    assert "3" in html
    assert "61,666" in html
    assert "Январь" in html
    assert "60,000" in html
    assert "Аналитическое заключение" in html
    assert "пик" in html
    assert "│" not in html
    assert "<pre>" not in html


def test_build_table_generator_pack_one_screen_with_insights() -> None:
    pack = build_table_generator_pack(
        MONTHLY_JSON,
        ai_insights="Стабильный рост выручки.",
    )
    assert pack is not None
    assert "ИТОГО" in pack.telegram_caption_html
    assert "60,000" in pack.telegram_caption_html
    assert "Аналитическое заключение" in pack.telegram_caption_html
    assert "<pre>" not in pack.telegram_caption_html


def test_compute_table_column_metrics_skips_existing_total_row() -> None:
    from services.table_text_response import compute_table_column_metrics

    rows = [
        ["Месяц", "Выручка"],
        ["Январь", "60000"],
        ["Итого", "60000"],
    ]
    metrics = compute_table_column_metrics(rows)
    assert metrics is not None
    assert metrics.total == 60_000
    assert len(metrics.data_rows) == 1


def _wb_sales_rows() -> list[list[str]]:
    return [
        [
            "Бренд",
            "Наименование",
            "Выкупили, шт.",
            "К перечислению за товар, руб.",
        ],
        ["ACME", "Стакан керамический большой", "2", "1200.50"],
        ["ACME", "Стакан керамический большой", "3", "1800.00"],
        ["ACME", "Кружка путешественника", "1", "950.00"],
    ]


def test_standard_report_on_wb_xlsx_uses_generic_caption() -> None:
    from services.table_processing_worker import sync_table_processing_from_rows

    result = sync_table_processing_from_rows(
        _wb_sales_rows(),
        "standard_report",
        title="Продажи_август",
    )
    assert result is not None
    caption = result.telegram_caption_html
    assert "WILDBERRIES" not in caption
    assert "ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ" not in caption
    assert "ИТОГО" in caption


def test_wb_finance_subrole_on_wb_xlsx_uses_express_caption() -> None:
    from services.table_processing_worker import sync_table_processing_from_rows

    result = sync_table_processing_from_rows(
        _wb_sales_rows(),
        "wb_ozon_finance",
        title="Продажи",
    )
    assert result is not None
    assert "ФИНАНСОВЫЙ ЭКСПРЕСС-АНАЛИЗ" in result.telegram_caption_html

