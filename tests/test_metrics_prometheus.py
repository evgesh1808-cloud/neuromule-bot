"""PR-L: тесты на ``services.metrics_prometheus.to_prometheus_text``.

Покрытие:

* пустой snapshot → пустая строка;
* counter без меток / с метками;
* точки в имени → подчёркивания;
* histograms сериализуются как ``summary`` (count / sum / quantile=0,1);
* экранирование label values (кавычки, бэкслэш, перенос строки);
* детерминированный порядок строк (sorted);
* интеграция с реальным ``metrics.snapshot()``;
* HTTP-роут ``GET /metrics`` отдаёт корректный Content-Type и тело.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from services import metrics
from services.metrics_http import build_metrics_app
from services.metrics_prometheus import to_prometheus_text


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


# ── Базовая сериализация ─────────────────────────────────────────────────


def test_empty_snapshot_returns_empty_string():
    assert to_prometheus_text({"counters": {}, "histograms": {}}) == ""


def test_counter_without_labels():
    out = to_prometheus_text({"counters": {"payment.success": 5}, "histograms": {}})
    assert "# TYPE payment_success counter" in out
    assert "payment_success 5" in out


def test_counter_with_labels_renders_inside_braces():
    snap = {
        "counters": {"payment.success{method=r,pack=MINI}": 3},
        "histograms": {},
    }
    out = to_prometheus_text(snap)
    assert "# TYPE payment_success counter" in out
    assert 'payment_success{method="r",pack="MINI"} 3' in out


def test_dots_in_metric_name_become_underscores():
    snap = {"counters": {"a.b.c.d": 1}, "histograms": {}}
    out = to_prometheus_text(snap)
    assert "a_b_c_d 1" in out
    assert "a.b.c.d" not in out  # имя должно быть полностью переписано


def test_multiple_counters_same_name_share_type_header():
    snap = {
        "counters": {
            "throttle.blocked{kind=callback}": 4,
            "throttle.blocked{kind=message}": 7,
        },
        "histograms": {},
    }
    out = to_prometheus_text(snap)
    # # TYPE строка ровно одна для имени, не дважды.
    assert out.count("# TYPE throttle_blocked counter") == 1
    assert 'throttle_blocked{kind="callback"} 4' in out
    assert 'throttle_blocked{kind="message"} 7' in out


# ── Гистограммы → summary ────────────────────────────────────────────────


def test_histogram_serializes_as_summary_with_quantiles():
    snap = {
        "counters": {},
        "histograms": {
            "gc.phase.duration_ms{gen=0}": {
                "count": 3,
                "sum": 44.5,
                "min": 7.0,
                "max": 25.5,
            }
        },
    }
    out = to_prometheus_text(snap)
    assert "# TYPE gc_phase_duration_ms summary" in out
    assert 'gc_phase_duration_ms_count{gen="0"} 3' in out
    assert "gc_phase_duration_ms_sum{gen=\"0\"} 44.5" in out
    assert 'gc_phase_duration_ms{gen="0",quantile="0"} 7' in out
    assert 'gc_phase_duration_ms{gen="0",quantile="1"} 25.5' in out


def test_histogram_without_labels_produces_minimal_sample_set():
    snap = {
        "counters": {},
        "histograms": {
            "latency_ms": {"count": 1, "sum": 42.0, "min": 42.0, "max": 42.0}
        },
    }
    out = to_prometheus_text(snap)
    assert "# TYPE latency_ms summary" in out
    assert "latency_ms_count 1" in out
    assert "latency_ms_sum 42" in out
    assert 'latency_ms{quantile="0"} 42' in out
    assert 'latency_ms{quantile="1"} 42' in out


# ── Экранирование label values ───────────────────────────────────────────


def test_label_value_with_quote_is_escaped():
    snap = {"counters": {'msg.unknown{reason=he said "hi"}': 1}, "histograms": {}}
    out = to_prometheus_text(snap)
    # внутри кавычек: " → \"
    assert 'reason="he said \\"hi\\""' in out


def test_label_value_with_backslash_is_escaped():
    snap = {"counters": {"x{path=C:\\Users}": 1}, "histograms": {}}
    out = to_prometheus_text(snap)
    assert 'path="C:\\\\Users"' in out


def test_label_value_with_newline_is_escaped():
    snap = {"counters": {"x{val=line1\nline2}": 1}, "histograms": {}}
    out = to_prometheus_text(snap)
    assert 'val="line1\\nline2"' in out


# ── Детерминизм порядка ──────────────────────────────────────────────────


def test_output_order_is_sorted_by_metric_name():
    snap = {
        "counters": {"zeta": 1, "alpha": 1, "mike": 1},
        "histograms": {},
    }
    out = to_prometheus_text(snap)
    # Имена идут в алфавитном порядке: alpha → mike → zeta.
    pos_a = out.index("alpha 1")
    pos_m = out.index("mike 1")
    pos_z = out.index("zeta 1")
    assert pos_a < pos_m < pos_z


def test_output_ends_with_newline():
    out = to_prometheus_text({"counters": {"a": 1}, "histograms": {}})
    assert out.endswith("\n")


# ── Интеграция с настоящим metrics.snapshot() ───────────────────────────


def test_real_snapshot_round_trip():
    metrics.incr("payment.success", {"method": "r", "pack": "MINI"})
    metrics.incr("payment.success", {"method": "r", "pack": "MINI"})
    metrics.incr("throttle.blocked", {"kind": "callback"})
    metrics.observe("gc.phase.duration_ms", 12.5, {"gen": "0"})

    out = to_prometheus_text(metrics.snapshot())

    assert 'payment_success{method="r",pack="MINI"} 2' in out
    assert 'throttle_blocked{kind="callback"} 1' in out
    assert 'gc_phase_duration_ms_count{gen="0"} 1' in out


# ── HTTP-роут /metrics ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_route_returns_text_plain_with_prometheus_body():
    metrics.incr("payment.success", {"method": "x", "pack": "BIG"})
    metrics.observe("gc.phase.duration_ms", 10.0, {"gen": "1"})

    app = build_metrics_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert 'payment_success{method="x",pack="BIG"} 1' in body
    assert "# TYPE gc_phase_duration_ms summary" in body


@pytest.mark.asyncio
async def test_metrics_route_with_empty_storage_returns_empty_body():
    app = build_metrics_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert resp.text == ""
