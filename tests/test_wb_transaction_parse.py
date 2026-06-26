"""Тесты построчной агрегации еженедельного отчёта WB."""

from __future__ import annotations

import pytest

from services.file_processor import compute_seller_matrix_etl
from services.table_text_response import compute_wb_marketplace_metrics
from services.table_wb_finance_ai import compute_wb_finance_prompt_metrics
from services.table_xlsx_preprocess import compute_marketplace_revenue_total
from services.wb_transaction_parse import aggregate_wb_transactions, is_valid_wb_sku


def _weekly_matrix() -> list[list[str]]:
  """Детализация: посуда 13 шт., стаканы 14 шт., кредит и хранение отдельно."""
  rows = [
      [
          "Предмет",
          "Артикул поставщика",
          "Тип документа",
          "Обоснование для оплаты",
          "Кол-во",
          "К перечислению продавцу за реализованный товар",
          "Услуги по доставке товара покупателю",
      ],
  ]
  for _ in range(13):
      rows.append(
          [
              "Посуда",
              "DISH-01",
              "Продажа",
              "Продажа",
              "1",
              "500",
              "30",
          ]
      )
  for _ in range(14):
      rows.append(
          [
              "Стаканы",
              "CUP-01",
              "Продажа",
              "Продажа",
              "1",
              "400",
              "25",
          ]
      )
  rows.extend(
      [
          ["—", "—", "Удержание", "Стоимость хранения", "", "-2782.27", ""],
          [
              "",
              "",
              "Удержание",
              "Предоставление кредита по договору 2025073100386",
              "",
              "-15000",
              "",
          ],
          ["", "", "Удержание", "Оплата продвижения", "", "-1200", ""],
      ]
  )
  return rows


def test_weekly_sales_and_buyout() -> None:
    matrix = _weekly_matrix()
    agg = aggregate_wb_transactions(matrix)
    assert agg is not None
    assert agg.sales_qty == pytest.approx(27.0)
    assert agg.revenue_from_sales == pytest.approx(13 * 500 + 14 * 400)
    assert agg.buyout_coef_pct == pytest.approx(100.0)
    assert agg.storage_cost == pytest.approx(2782.27)
    assert agg.credit_deductions == pytest.approx(15000.0)
    assert agg.total_advertising_cost == pytest.approx(1200.0)


def test_storno_adjusts_sku_revenue() -> None:
    matrix = [
        [
            "Предмет",
            "Артикул поставщика",
            "Тип документа",
            "Обоснование для оплаты",
            "Кол-во",
            "К перечислению продавцу за реализованный товар",
        ],
        ["Посуда", "DISH-01", "Продажа", "Продажа", "1", "1000"],
        ["Посуда", "DISH-01", "Сторно", "Корректировка", "1", "-200"],
    ]
    agg = aggregate_wb_transactions(matrix)
    assert agg is not None
    bucket = agg.sku_buckets[("Посуда", "DISH-01")]
    assert bucket.revenue == pytest.approx(800.0)


def test_buyout_sales_over_sales_plus_returns() -> None:
    from services.file_processor import compute_buyout_coef_pct

    matrix = [
        [
            "Предмет",
            "Артикул поставщика",
            "Тип документа",
            "Обоснование для оплаты",
            "Кол-во",
            "К перечислению продавцу за реализованный товар",
        ],
        ["Товар", "SKU-1", "Продажа", "Продажа", "1", "500"],
        ["Товар", "SKU-1", "Возврат", "Возврат", "1", "100"],
    ]
    agg = aggregate_wb_transactions(matrix)
    assert agg is not None
    assert agg.sales_qty == pytest.approx(1.0)
    assert agg.returns_qty == pytest.approx(1.0)
    assert agg.buyout_coef_pct == pytest.approx(compute_buyout_coef_pct(1.0, 1.0))
def test_drr_excludes_credit_and_storage() -> None:
    matrix = _weekly_matrix()
    revenue = compute_marketplace_revenue_total(matrix)
    metrics = compute_wb_marketplace_metrics(matrix, revenue_total=revenue)
    assert metrics is not None
    assert metrics.sales_qty == pytest.approx(27.0)
    assert metrics.buyout_coef_pct > 0
    expected_drr = 1200.0 / revenue * 100.0
    assert metrics.ad_load_pct == pytest.approx(expected_drr)
    assert metrics.ad_load_pct < 20.0
    assert metrics.credit_deductions == pytest.approx(15000.0)
    assert metrics.storage_cost == pytest.approx(2782.27)


def test_credit_loss_verdict() -> None:
    matrix = _weekly_matrix()
    revenue = compute_marketplace_revenue_total(matrix)
    wb_metrics = compute_wb_marketplace_metrics(matrix, revenue_total=revenue)
    prompt = compute_wb_finance_prompt_metrics(
        revenue, wb_metrics, matrix_rows=matrix, platform="wildberries"
    )
    assert prompt is not None
    assert prompt.clear_profit < 0
    assert prompt.operational_profit > 0
    assert "кредит" in prompt.verdict.lower()


def test_empty_sku_excluded_from_abc() -> None:
    matrix = _weekly_matrix()
    revenue = compute_marketplace_revenue_total(matrix)
    etl = compute_seller_matrix_etl(matrix, revenue_total=revenue)
    assert etl is not None
    names = {s.name for s in etl.abc_group_a} | {s.name for s in etl.abc_group_c}
    assert "—" not in names
    assert all(is_valid_wb_sku(s.name, s.article_id) for s in etl.sku_catalog)


def test_is_valid_wb_sku_rejects_dash_dash() -> None:
    assert not is_valid_wb_sku("—", "—")
    assert is_valid_wb_sku("Посуда", "DISH-01")
