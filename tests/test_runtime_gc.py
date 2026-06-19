"""Тесты контракта `services/runtime_gc.py` (pytest + pytest-mock).

Ровно 9 кейсов:

1. setup → один disable + один freeze;
2. setup идемпотентен при уже выключенном GC;
3. run_gc_cycle вызывает collect для gen 0,1,2 строго в порядке;
4. между фазами есть ≥3 ``asyncio.sleep(0)`` (через ``mocker.spy``);
5. фактический collect идёт через ``loop.run_in_executor`` (non-blocking);
6. долгая фаза > ``SLOW_GC_THRESHOLD_SEC`` (эмулируется через
   ``time.perf_counter``) → ``WARNING``;
7. падение в gen1 не останавливает gen0/gen2; идёт ``ERROR`` с stacktrace;
8. ``task.cancel()`` корректно завершает ``controlled_gc_loop``;
9. интервал между тиками ``controlled_gc_loop`` соответствует ``interval_sec``
   (через ``mocker.spy(asyncio, "sleep")``).
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from pytest_mock import MockerFixture

from services import runtime_gc


# ─────────────────────────────────────────────────────────────────────────
# 1–2. setup_optimized_gc
# ─────────────────────────────────────────────────────────────────────────


def test_setup_disables_auto_gc_and_freezes(mocker: MockerFixture) -> None:
    mocker.patch.object(runtime_gc.gc, "isenabled", return_value=True)
    mocker.patch.object(runtime_gc.gc, "get_freeze_count", return_value=42)
    spy_freeze = mocker.patch.object(runtime_gc.gc, "freeze")
    spy_disable = mocker.patch.object(runtime_gc.gc, "disable")

    runtime_gc.setup_optimized_gc()

    spy_freeze.assert_called_once_with()
    spy_disable.assert_called_once_with()


def test_setup_is_idempotent_when_already_disabled(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(runtime_gc.gc, "isenabled", return_value=False)
    spy_freeze = mocker.patch.object(runtime_gc.gc, "freeze")
    spy_disable = mocker.patch.object(runtime_gc.gc, "disable")

    runtime_gc.setup_optimized_gc()

    spy_freeze.assert_not_called()
    spy_disable.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# 3–7. run_gc_cycle
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_gc_cycle_calls_three_generations_in_order(
    mocker: MockerFixture,
) -> None:
    spy = mocker.patch.object(runtime_gc.gc, "collect", return_value=11)

    await runtime_gc.run_gc_cycle()

    assert spy.call_count == 3
    assert [c.args[0] for c in spy.call_args_list] == [0, 1, 2]


@pytest.mark.asyncio
async def test_run_gc_cycle_yields_event_loop_between_phases(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(runtime_gc.gc, "collect", return_value=0)
    sleep_spy = mocker.spy(asyncio, "sleep")

    await runtime_gc.run_gc_cycle()

    zero_sleeps = [c for c in sleep_spy.call_args_list if c.args == (0,)]
    assert len(zero_sleeps) >= 3


@pytest.mark.asyncio
async def test_run_gc_cycle_runs_collect_in_executor(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(runtime_gc.gc, "collect", return_value=0)
    loop = asyncio.get_running_loop()
    exec_spy = mocker.spy(loop, "run_in_executor")

    await runtime_gc.run_gc_cycle()

    assert exec_spy.call_count == 3
    for call, expected_gen in zip(exec_spy.call_args_list, (0, 1, 2)):
        assert call.args[0] is None
        assert call.args[1] is runtime_gc.gc.collect
        assert call.args[2] == expected_gen


@pytest.mark.asyncio
async def test_run_gc_cycle_logs_slow_phase_as_warning(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch.object(runtime_gc.gc, "collect", return_value=99)
    # Эмулируем медленную фазу через подмену time.perf_counter:
    # каждая пара (start, end) даёт elapsed > SLOW_GC_THRESHOLD_SEC.
    elapsed = runtime_gc.SLOW_GC_THRESHOLD_SEC + 0.25
    ticks = iter([0.0, elapsed, 1.0, 1.0 + elapsed, 2.0, 2.0 + elapsed])
    mocker.patch.object(
        runtime_gc.time, "perf_counter", side_effect=lambda: next(ticks)
    )
    caplog.set_level(logging.DEBUG, logger="services.runtime_gc")

    await runtime_gc.run_gc_cycle()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3
    assert all("collected=99" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_run_gc_cycle_survives_collect_exception(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    async def fake_collect(gen: int) -> tuple[int, float]:
        if gen == 1:
            raise RuntimeError("finalizer failed")
        return 0, 0.001

    mocker.patch.object(
        runtime_gc, "_collect_generation_async", side_effect=fake_collect
    )
    caplog.set_level(logging.ERROR, logger="services.runtime_gc")

    stats = await runtime_gc.run_gc_cycle()

    assert "gen0_objects" in stats
    assert "gen2_objects" in stats
    assert "gen1_objects" not in stats

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None


# ─────────────────────────────────────────────────────────────────────────
# 8–9. controlled_gc_loop
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_controlled_gc_loop_can_be_cancelled(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(runtime_gc, "run_gc_cycle", new=mocker.AsyncMock())

    task = asyncio.create_task(runtime_gc.controlled_gc_loop(interval_sec=10))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_controlled_gc_loop_runs_cycle_each_interval(
    mocker: MockerFixture,
) -> None:
    cycle_spy = mocker.patch.object(
        runtime_gc, "run_gc_cycle", new=mocker.AsyncMock(return_value={})
    )
    sleep_spy = mocker.spy(asyncio, "sleep")

    task = asyncio.create_task(runtime_gc.controlled_gc_loop(interval_sec=0.02))
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Цикл засыпал именно на 0.02 — это и есть «интервал между итерациями».
    interval_sleeps = [c for c in sleep_spy.call_args_list if c.args == (0.02,)]
    assert len(interval_sleeps) >= 2
    # А ``run_gc_cycle`` вызывался не реже, чем количество прошедших интервалов.
    assert cycle_spy.await_count >= 2
