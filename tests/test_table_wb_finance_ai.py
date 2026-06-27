"""Промпт и метрики ИИ-консалтинга WB/Ozon finance."""

from __future__ import annotations

import json

import pytest

from content.chat_prompt import WB_ANALYTICS_SYSTEM_PROMPT, build_wb_marketplace_finance_system_prompt
from services.table_text_response import FINANCE_REPORT_BUILD, compute_wb_marketplace_metrics
from services.table_wb_finance_ai import (
    append_wb_finance_mini_app_cta,
    build_wb_finance_json_user_message,
    build_wb_finance_openrouter_prompt_pair,
    build_wb_finance_system_prompt_from_totals,
    build_wb_mpstats_ai_context,
    compute_business_score,
    compute_fomo_lost_rub,
    compute_wb_finance_prompt_metrics,
    derive_business_verdict,
)


def test_wb_analytics_system_prompt_cfo_v8_static() -> None:
    prompt = build_wb_marketplace_finance_system_prompt()
    assert prompt is WB_ANALYTICS_SYSTEM_PROMPT
    assert "CFO" in prompt
    assert "final_metrics_json" in prompt or "JSON" in prompt
    assert "Проблемные зоны" in prompt
    assert "Балласт" in prompt
    assert "неликвид" in prompt.lower()
    assert "Senior ИИ-Аналитик" not in prompt
    assert "ИНДЕКС ЗДОРОВЬЯ БИЗНЕСА" in prompt
    assert "СВЕТОФОР ЭФФЕКТИВНОСТИ" in prompt
    assert "КАЛЬКУЛЯТОР ПОТЕРЬ" in prompt
    assert "2000 символов" in prompt
    assert "ABC-АНАЛИЗ ПРОДАЖ" in prompt
    assert "BENOVY" not in prompt
    assert "cfo-v12" in prompt
    assert "20%" in prompt
    assert "group_A" in prompt
    assert "loss_calculator" in prompt
    assert "traffic_light" in prompt
    assert "health_index" in prompt
    assert "СЛУЖЕБНЫЕ ПРАВИЛА" not in prompt
    assert "ПЛАН ДЕЙСТВИЙ ДЛЯ ПРЕДПРИНИМАТЕЛЯ" in prompt
    assert "ГЛАВНЫЙ АНАЛИТИЧЕСКИЙ ВЫВОД" in prompt
    assert "ГЛАВНЫЙ ВЫВОД ИИ" not in prompt
    assert "ОБЩАЯ ВЫРУЧКА" in prompt
    assert "ИИ-ПЛАН" not in prompt


def test_build_wb_finance_json_user_message_wraps_payload() -> None:
    payload = {"finance": {"total_revenue": 1000.0}}
    user = build_wb_finance_json_user_message(payload)
    assert "1000.0" in user
    assert "CFO" in user
    assert "JSON" in user
    assert "ИИ»" in user or "«ИИ»" in user


def test_build_wb_finance_openrouter_prompt_pair_from_matrix() -> None:
    matrix = [
        [
            "Бренд",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["ACME", "SKU-A1", "70", "90", "5", "3500", "100000", "30"],
    ]
    pair = build_wb_finance_openrouter_prompt_pair(matrix, revenue_total=100_000.0)
    assert pair is not None
    system, user = pair
    assert "cfo-v12" in system
    data = json.loads(user.split("\n\n", 1)[-1])
    assert data["finance"]["total_revenue"] == 100_000.0
    assert "health_index" in data
    assert "traffic_light" in data


def test_compute_wb_finance_prompt_metrics_from_etl() -> None:
    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["Футболка Premium", "70", "90", "5", "3500", "100000", "30"],
        ["DEAD", "0", "10", "0", "500", "0", "100"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    assert metrics.revenue == 100_000.0
    assert metrics.business_score >= 1.0


def test_build_wb_finance_system_prompt_from_totals() -> None:
    wb = compute_wb_marketplace_metrics(
        [
            ["Предмет", "К перечислению, руб."],
            ["A", "100000"],
        ],
        revenue_total=100_000.0,
    )
    system = build_wb_finance_system_prompt_from_totals(100_000.0, wb)
    assert system is not None
    assert "cfo-v12" in system


def test_compute_business_score_bounds() -> None:
    high = compute_business_score(
        profitability_pct=20.0,
        ad_load_pct=10.0,
        buyout_coef_pct=75.0,
        worst_unit_net=50.0,
    )
    assert 8.0 <= high <= 10.0
    low = compute_business_score(
        profitability_pct=2.0,
        ad_load_pct=30.0,
        buyout_coef_pct=30.0,
        worst_unit_net=-100.0,
    )
    assert 1.0 <= low <= 5.0


def test_derive_business_verdict_high_score() -> None:
    verdict = derive_business_verdict(
        business_score=8.5,
        profitability_pct=20.0,
        ad_load_pct=10.0,
        buyout_coef_pct=70.0,
        worst_unit_label=None,
    )
    assert "масштабирован" in verdict.lower() or "маржинальность" in verdict.lower()


def test_compute_fomo_lost_rub() -> None:
    wb = compute_wb_marketplace_metrics(
        [
            ["Предмет", "К перечислению, руб.", "Выкупили, шт.", "Доставки, шт.", "Возвраты, шт."],
            ["A", "100000", "30", "100", "20"],
        ],
        revenue_total=100_000.0,
    )
    lost, parts = compute_fomo_lost_rub(100_000.0, wb)
    assert lost >= 0.0
    assert isinstance(parts, tuple)


def test_append_wb_finance_mini_app_cta_empty() -> None:
    html = append_wb_finance_mini_app_cta("<b>test</b>")
    assert "test" in html


def test_sanitize_wb_finance_html_strips_technical_parentheses() -> None:
    from services.table_wb_finance_ai import sanitize_wb_finance_html

    raw = (
        "📦 ABC-АНАЛИЗ МАТРИЦЫ (локальный ETL, не пересчитывай)\n"
        "🟢 ЗОНА УСПЕХА (по 2–3 предложения на зону, без воды)\n"
        "📋 ПЛАН (каждый шаг — 1 предложение)"
    )
    cleaned = sanitize_wb_finance_html(raw)
    assert "локальный ETL" not in cleaned
    assert "по 2–3 предложения" not in cleaned
    assert "каждый шаг" not in cleaned
    assert "ABC-АНАЛИЗ ПРОДАЖ" in cleaned


def test_build_wb_mpstats_ai_context_full_group_c() -> None:
    matrix = [
        [
            "Бренд",
            "Артикул",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["A", "1", "50", "60", "2", "1000", "80000", "10"],
        ["B", "2", "10", "20", "1", "500", "15000", "5"],
        ["C", "3", "5", "10", "0", "200", "5000", "100"],
    ]
    ctx = build_wb_mpstats_ai_context(matrix, revenue_total=100_000.0)
    assert len(ctx["abc_analysis"]["group_C"]) == ctx["abc_analysis"]["total_group_c_count"]
    assert isinstance(ctx["problem_zones"]["ballast"], list)


def test_build_matrix_problem_zones_block() -> None:
    from services.table_wb_finance_ai import build_matrix_problem_zones_block
    from services.file_processor import compute_seller_matrix_etl

    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["DEAD", "0", "10", "5", "500", "0", "100"],
    ]
    etl = compute_seller_matrix_etl(matrix, revenue_total=0.0)
    block = build_matrix_problem_zones_block(etl)
    assert "Неликвид" in block or "Балласт" in block or "проблемных" in block.lower()


def test_aggregate_matrix_display_tail_counts() -> None:
    from services.file_processor import MatrixAbcSku, MatrixOosForecast, MatrixSkuDetail, SellerMatrixEtl
    from services.table_wb_finance_ai import aggregate_matrix_display

    group_a = tuple(
        MatrixAbcSku(
            name=f"A{i}",
            article_id=f"art{i}",
            revenue=10_000.0 * i,
            net_profit=1_000.0 * (12 - i),
            buyout_pct=70.0,
            abc_group="A",
        )
        for i in range(1, 13)
    )
    group_c = tuple(
        MatrixAbcSku(
            name=f"C{i}",
            article_id=f"c{i}",
            revenue=float(i * 100),
            net_profit=-50.0,
            buyout_pct=10.0,
            abc_group="C",
        )
        for i in range(1, 9)
    )
    catalog = tuple(
        MatrixSkuDetail(
            name=sku.name,
            article_id=sku.article_id,
            revenue=sku.revenue,
            net_profit=sku.net_profit,
            buyout_pct=sku.buyout_pct,
            abc_group=sku.abc_group,
            stock_qty=50.0 if sku.name == "C8" else 0.0,
        )
        for sku in (*group_a, *group_c)
    )
    oos = tuple(
        MatrixOosForecast(
            label=sku.name,
            stock_qty=50.0 if sku.name == "C8" else 0.0,
            sales_period_qty=1.0 if sku.revenue > 0 else 0.0,
            days_until_stockout=None,
            risk_out_of_stock=False,
        )
        for sku in group_c
    )
    etl = SellerMatrixEtl(
        abc_group_a=group_a,
        abc_group_c=group_c,
        abc_a_leader="A1",
        logistics_fomo_rub=0.0,
        logistics_fomo_detail="",
        oos_forecasts=oos,
        oos_critical_sku=None,
        oos_critical_days=None,
        sku_catalog=catalog,
    )
    agg = aggregate_matrix_display(etl)
    assert len(agg.abc_a_display_lines) == 5
    assert agg.tail_a_count == 7
    assert len(agg.abc_c_display_lines) == 5
    assert agg.tail_c_count == 3
    assert agg.tail_c_revenue == round(600.0 + 700.0 + 800.0, 2)


def test_build_wb_finance_express_html_local_abc_header() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        ["Предмет", "К перечислению, руб.", "Выкупили, шт.", "Доставки, шт.", "Возвраты, шт."],
        ["WRAPPER", "100000", "70", "90", "5"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "ABC-АНАЛИЗ ПРОДАЖ" in html
    assert "cfo-v12" in html


def test_build_wb_finance_express_html_local_no_ii_word() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        ["Предмет", "К перечислению, руб."],
        ["A", "50000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=50_000.0)
    metrics = compute_wb_finance_prompt_metrics(50_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, wb)
    assert "ИИ-Аналитик" not in html
    assert "ИИ-ПЛАН" not in html


def test_build_wb_finance_express_html_local_traffic_light() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "К перечислению, руб.",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
        ],
        ["GOOD", "90000", "70", "90", "5", "3000"],
        ["BAD", "10000", "0", "20", "10", "800"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, wb)
    assert "СВЕТОФОР ЭФФЕКТИВНОСТИ" in html
    assert "ЗОНА УСПЕХА" in html


def test_build_wb_finance_express_html_local_loss_calculator() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "К перечислению, руб.",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
        ],
        ["RET", "50000", "10", "30", "15", "2000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=50_000.0)
    metrics = compute_wb_finance_prompt_metrics(50_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, wb)
    assert "КАЛЬКУЛЯТОР ПОТЕРЬ" in html
    assert "Логистика возвратов" in html or "покатуш" in html.lower()


def test_build_wb_finance_express_html_local_health_index() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        ["Предмет", "К перечислению, руб."],
        ["A", "100000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=100_000.0)
    metrics = compute_wb_finance_prompt_metrics(100_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "ИНДЕКС ЗДОРОВЬЯ" in html


def test_build_wb_finance_express_html_local_matrix_zones() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "Выкупили, шт.",
            "Доставки, шт.",
            "Возвраты, шт.",
            "Логистика, руб.",
            "К перечислению, руб.",
            "Остаток на складе, шт.",
        ],
        ["DEAD", "0", "10", "5", "500", "0", "100"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=1.0)
    metrics = compute_wb_finance_prompt_metrics(1.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "Проблемные зоны" in html
    assert "Неликвид" in html or "Балласт" in html or "неликвид" in html.lower()


def test_build_wb_finance_express_html_local_plan() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [["Предмет", "К перечислению, руб."], ["A", "10000"]]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=10_000.0)
    metrics = compute_wb_finance_prompt_metrics(10_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, None)
    assert "ПЛАН ДЕЙСТВИЙ" in html


def test_oos_zero_stock_forecast_and_plan_aligned() -> None:
    """Нулевой остаток: план и прогноз читают oos_zero_stock_items, без «через 0 дн.»."""
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        [
            "Предмет",
            "К перечислению, руб.",
            "Выкупили, шт.",
            "Остаток на складе, шт.",
        ],
        ["DEAD_SKU", "10000", "14", "0"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=10_000.0)
    metrics = compute_wb_finance_prompt_metrics(10_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    assert len(metrics.oos_zero_stock_items) == 1
    assert "<pre>" in metrics.oos_forecast_line
    assert "🔴 ТОВАР ПОЛНОСТЬЮ ЗАКОНЧИЛСЯ" in metrics.oos_forecast_line
    assert "  •" in metrics.oos_forecast_line
    assert "0 шт" not in metrics.oos_forecast_line.lower()
    assert "через" not in metrics.oos_forecast_line.lower()
    assert "критических рисков обнуления остатков не выявлено" not in metrics.oos_forecast_line

    html = build_wb_finance_express_html_local(metrics, wb)
    assert "Срочно закупите" in html
    assert "товар закончился" in html
    assert "🔴 ТОВАР ПОЛНОСТЬЮ ЗАКОНЧИЛСЯ" in html
    assert "критических рисков обнуления остатков не выявлено" not in html


def test_oos_multiple_zero_stock_deficit_message() -> None:
    from services.file_processor import (
        MatrixOosForecast,
        MatrixSkuDetail,
        SellerMatrixEtl,
    )
    from services.table_wb_finance_ai import _build_oos_forecast_line, _collect_etl_dynamic_slices

    catalog = (
        MatrixSkuDetail(
            name="A",
            article_id="a1",
            revenue=1000.0,
            net_profit=100.0,
            buyout_pct=80.0,
            abc_group="A",
            stock_qty=0.0,
            sales_qty=7.0,
        ),
        MatrixSkuDetail(
            name="B",
            article_id="b1",
            revenue=500.0,
            net_profit=50.0,
            buyout_pct=70.0,
            abc_group="B",
            stock_qty=0.0,
            sales_qty=3.0,
        ),
    )
    forecasts = (
        MatrixOosForecast("A", 0.0, 7.0, 0.0, True),
        MatrixOosForecast("B", 0.0, 3.0, 0.0, True),
    )
    etl = SellerMatrixEtl(
        abc_group_a=(),
        abc_group_c=(),
        abc_a_leader="A",
        logistics_fomo_rub=0.0,
        logistics_fomo_detail="",
        oos_forecasts=forecasts,
        oos_critical_sku="A",
        oos_critical_days=0.0,
        sku_catalog=catalog,
    )
    *_, oos_zero, oos_critical, _ = _collect_etl_dynamic_slices(etl)
    assert len(oos_zero) == 2
    assert not oos_critical
    line = _build_oos_forecast_line(etl, oos_zero, oos_critical)
    assert "<pre>" in line
    assert "дефицит по 2" in line
    assert "Мониторинг запасов" in line
    assert "  • A (арт. a1) — 🔴 ТОВАР ПОЛНОСТЬЮ ЗАКОНЧИЛСЯ" in line
    assert "  • B (арт. b1) — 🔴 ТОВАР ПОЛНОСТЬЮ ЗАКОНЧИЛСЯ" in line
    assert "критических рисков" not in line


def test_oos_critical_and_zero_stock_column_list() -> None:
    from services.file_processor import MatrixOosForecast, MatrixSkuDetail, SellerMatrixEtl
    from services.table_wb_finance_ai import _build_oos_forecast_line, _collect_etl_dynamic_slices

    catalog = (
        MatrixSkuDetail(
            name="DEAD",
            article_id="d1",
            revenue=100.0,
            net_profit=50.0,
            buyout_pct=80.0,
            abc_group="A",
            stock_qty=0.0,
            sales_qty=5.0,
        ),
        MatrixSkuDetail(
            name="LOW",
            article_id="l1",
            revenue=200.0,
            net_profit=40.0,
            buyout_pct=70.0,
            abc_group="B",
            stock_qty=3.0,
            sales_qty=7.0,
        ),
    )
    forecasts = (
        MatrixOosForecast("DEAD", 0.0, 5.0, 0.0, True),
        MatrixOosForecast("LOW", 3.0, 7.0, 2.0, True),
    )
    etl = SellerMatrixEtl(
        abc_group_a=(),
        abc_group_c=(),
        abc_a_leader="DEAD",
        logistics_fomo_rub=0.0,
        logistics_fomo_detail="",
        oos_forecasts=forecasts,
        oos_critical_sku="LOW",
        oos_critical_days=2.0,
        sku_catalog=catalog,
    )
    *_, oos_zero, oos_critical, _ = _collect_etl_dynamic_slices(etl)
    assert len(oos_zero) == 1
    assert len(oos_critical) == 1
    line = _build_oos_forecast_line(etl, oos_zero, oos_critical)
    assert "дефицит по 2" in line
    assert "DEAD (арт. d1) — 🔴 ТОВАР ПОЛНОСТЬЮ ЗАКОНЧИЛСЯ" in line
    assert "LOW (арт. l1) — 🟡 СКОРО ЗАКОНЧИТСЯ" in line
    assert "0 шт" not in line
    assert "через" not in line
    assert "SKU &lt;bad&gt;" not in line  # sanity: escape only when needed


def test_build_wb_finance_express_html_pre_wrapper_and_escape() -> None:
    from services.table_wb_finance_ai import (
        build_wb_finance_express_html_local,
        compute_wb_finance_prompt_metrics,
    )

    matrix = [
        ["Предмет", "К перечислению, руб."],
        ["SKU <bad> & Co", "50000"],
    ]
    wb = compute_wb_marketplace_metrics(matrix, revenue_total=50_000.0)
    metrics = compute_wb_finance_prompt_metrics(50_000.0, wb, matrix_rows=matrix)
    assert metrics is not None
    html = build_wb_finance_express_html_local(metrics, wb)
    assert html.startswith("<pre>")
    assert html.endswith("</pre>")
    assert "&lt;bad&gt;" in html
    assert " &amp; " in html or "&amp;" in html


def test_compute_buyout_coef_pct_formula() -> None:
    from services.file_processor import compute_buyout_coef_pct

    assert compute_buyout_coef_pct(10.0, 5.0) == pytest.approx(66.666, rel=1e-3)
    assert compute_buyout_coef_pct(27.0, 0.0) == pytest.approx(100.0)
    assert compute_buyout_coef_pct(0.0, 5.0) == pytest.approx(0.0)


def test_find_column_index_synonyms() -> None:
    from services.file_processor import find_column_index

    headers = [
        "Артикул поставщика",
        "Цена розничная с учетом согласованной скидки",
        "Себестоимость",
        "Тип документа",
    ]
    assert find_column_index(headers, "sku") == 0
    assert find_column_index(headers, "sale_price") == 1
    assert find_column_index(headers, "cost") == 2
    assert find_column_index(headers, "operation_type") == 3
    assert find_column_index(headers, "cost", exclude_substrings=("xyz",)) == 2
    assert find_column_index(headers, "missing_key") is None


def test_build_final_metrics_json_cfo_v10() -> None:
    from services.file_processor import build_final_metrics_json
    from services.table_wb_finance_ai import build_wb_mpstats_ai_context

    matrix = [
        ["Предмет", "К перечислению, руб.", "Выкупили, шт.", "Возвраты, шт."],
        ["GOOD", "80000", "8", "2"],
    ]
    final = build_final_metrics_json(matrix, revenue_total=80_000.0)
    assert final.get("cfo_build") == FINANCE_REPORT_BUILD
    assert final["shop"]["total_revenue"] == 80_000.0
    assert "sku_catalog" in final
    assert "abc_analysis" in final

    ctx = build_wb_mpstats_ai_context(matrix, revenue_total=80_000.0)
    assert ctx.get("cfo_build") == FINANCE_REPORT_BUILD
    assert "finance" in ctx
    assert "health_index" in ctx
    assert "strategic_plan_hints" in ctx
