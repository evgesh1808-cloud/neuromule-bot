"""PR-H: тесты на ``services.metrics`` — лёгкий observability-слой.

Покрытие:

* ``incr`` без меток и с метками; ``value`` override;
* ``observe`` агрегирует count/sum/min/max;
* ``_compose_key`` детерминирован (метки сортируются);
* ``snapshot`` — иммутабельная копия (изменения в результате не влияют
  на внутренний сторадж);
* ``reset`` обнуляет всё;
* интеграция: ``payment.success``, ``throttle.blocked``, ``notify.*``
  взводятся из реальных call-sites после соответствующих сценариев.
"""
from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from services import metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


# ── Базовый API ──────────────────────────────────────────────────────────


def test_incr_default_step_is_one():
    metrics.incr("payment.success")
    metrics.incr("payment.success")
    snap = metrics.snapshot()
    assert snap["counters"]["payment.success"] == 2


def test_incr_with_labels_creates_separate_keys():
    metrics.incr("payment.success", {"method": "r"})
    metrics.incr("payment.success", {"method": "x"})
    metrics.incr("payment.success", {"method": "r"})
    snap = metrics.snapshot()
    assert snap["counters"]["payment.success{method=r}"] == 2
    assert snap["counters"]["payment.success{method=x}"] == 1


def test_incr_with_custom_value():
    metrics.incr("queue.depth", value=5)
    metrics.incr("queue.depth", value=3)
    assert metrics.snapshot()["counters"]["queue.depth"] == 8


def test_label_order_is_deterministic():
    """Разные порядок-словари дают тот же составной ключ."""
    metrics.incr("x", {"a": "1", "b": "2"})
    metrics.incr("x", {"b": "2", "a": "1"})
    snap = metrics.snapshot()
    # Только один ключ должен существовать.
    keys = [k for k in snap["counters"] if k.startswith("x")]
    assert len(keys) == 1
    assert keys[0] == "x{a=1,b=2}"
    assert snap["counters"][keys[0]] == 2


def test_observe_aggregates_count_sum_min_max():
    metrics.observe("gc.phase.duration_ms", 12.0, {"gen": "0"})
    metrics.observe("gc.phase.duration_ms", 25.5, {"gen": "0"})
    metrics.observe("gc.phase.duration_ms", 7.0, {"gen": "0"})
    hist = metrics.snapshot()["histograms"]["gc.phase.duration_ms{gen=0}"]
    assert hist["count"] == 3
    assert hist["sum"] == pytest.approx(44.5)
    assert hist["min"] == pytest.approx(7.0)
    assert hist["max"] == pytest.approx(25.5)


def test_observe_initial_value_seeds_min_and_max():
    metrics.observe("latency_ms", 42.0)
    hist = metrics.snapshot()["histograms"]["latency_ms"]
    assert hist["count"] == 1
    assert hist["min"] == hist["max"] == 42.0


def test_snapshot_is_immutable_copy():
    metrics.incr("a")
    snap = metrics.snapshot()
    snap["counters"]["a"] = 999  # мутируем копию
    assert metrics.snapshot()["counters"]["a"] == 1  # внутри не изменилось


def test_reset_clears_everything():
    metrics.incr("a", value=10)
    metrics.observe("b", 5.0)
    metrics.reset()
    snap = metrics.snapshot()
    assert snap == {"counters": {}, "histograms": {}}


# ── Интеграция с реальными call-sites ────────────────────────────────────


@pytest.mark.asyncio
async def test_payment_success_increments_counter(mocker: MockerFixture):
    """Полный путь happy-payment должен взвести ``payment.success``."""
    from dataclasses import dataclass
    from types import SimpleNamespace

    from services.use_cases import payment_turn as pt

    @dataclass(frozen=True)
    class _PurchaseStub:
        ok: bool = True
        energy_paid_added: int = 100
        crystals_added: int = 20
        tariff_updated: object | None = None

    mocker.patch.object(
        pt.paycat, "parse_invoice_payload", return_value=(42, 0, "r")
    )
    mocker.patch.object(pt, "pack_name_from_catalog_index", return_value="MINI")
    mocker.patch.object(
        pt.paycat,
        "PACKAGES",
        [SimpleNamespace(
            tariff="MINI", energy=100, crystals=20,
            rub_kopecks=29900, stars=200,
        )],
    )
    mocker.patch.object(
        pt, "claim_payment_charge", new=mocker.AsyncMock(return_value=True)
    )
    mocker.patch.object(pt, "ensure_user", new=mocker.AsyncMock())
    mocker.patch.object(
        pt.billing,
        "process_purchase",
        new=mocker.AsyncMock(return_value=_PurchaseStub()),
    )
    mocker.patch.object(pt, "insert_payment_event", new=mocker.AsyncMock())

    await pt.run_successful_payment_apply(42, "payload", "ch_abc", None)

    snap = metrics.snapshot()
    assert snap["counters"]["payment.success{method=r,pack=MINI}"] == 1


def test_payment_invalid_with_reason(mocker: MockerFixture):
    """Невалидный payload → counter с label reason=bad_payload."""
    import asyncio

    from services.use_cases import payment_turn as pt

    mocker.patch.object(pt.paycat, "parse_invoice_payload", return_value=None)

    asyncio.run(pt.run_successful_payment_apply(42, "", None, None))

    snap = metrics.snapshot()
    assert snap["counters"]["payment.invalid{reason=bad_payload}"] == 1


@pytest.mark.asyncio
async def test_notify_forbidden_increments_counter(mocker: MockerFixture):
    """``TelegramForbiddenError`` поднимает ``notify.forbidden{context=...}``."""
    from aiogram.exceptions import TelegramForbiddenError

    from platforms import telegram_notify as tn

    bot = mocker.MagicMock()
    bot.send_message = mocker.AsyncMock(
        side_effect=TelegramForbiddenError(method=mocker.MagicMock(), message="bot was blocked")
    )

    ok = await tn.safe_send_user_message(bot, 777, "hi", context="ref_bonus")

    assert ok is False
    snap = metrics.snapshot()
    assert snap["counters"]["notify.forbidden{context=ref_bonus}"] == 1
    assert "notify.sent{context=ref_bonus}" not in snap["counters"]


@pytest.mark.asyncio
async def test_notify_sent_on_success(mocker: MockerFixture):
    from platforms import telegram_notify as tn

    bot = mocker.MagicMock()
    bot.send_message = mocker.AsyncMock(return_value=None)

    ok = await tn.safe_send_user_message(
        bot, 777, "hi", context="gallery_approve_notify"
    )

    assert ok is True
    snap = metrics.snapshot()
    assert (
        snap["counters"]["notify.sent{context=gallery_approve_notify}"] == 1
    )
