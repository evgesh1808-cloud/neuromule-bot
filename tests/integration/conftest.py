"""Локальный conftest для integration-suite.

Содержит ``LatencyTracker`` — лёгкий хелпер для замера latency
операций внутри pytest-тестов и форматированной печати квантилей
(p50 / p95 / p99 / max).

Использование:

    async def test_something(pg_pool, latency):
        async def claim_once(i):
            async with latency.measure():
                async with db_transaction(pg_pool) as conn:
                    await PaymentRepository(conn).claim_payment_charge(...)
        await asyncio.gather(*(claim_once(i) for i in range(200)))
        latency.report("concurrent_claim_30")

Чтобы увидеть отчёт в выводе pytest, гоняйте с ``-s``:

    pytest tests/integration -v -s -m integration

Без ``-s`` pytest капчит stdout — отчёт всё равно появится в случае
**падения** теста (capture flush'ится в diagnostics), но при успехе
будет скрыт. Это интенциональное поведение pytest, не наш баг.
"""

from __future__ import annotations

import statistics
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

import pytest


@dataclass
class LatencyTracker:
    """Простой накопитель latency-сэмплов с печатью отчёта.

    Не зависит от ``services.metrics`` (это integration-suite, локальные
    числа важнее глобальных счётчиков). Если потребуется stream'ить в
    общий metrics-namespace — добавим ``metrics.observe`` рядом.
    """

    samples: list[float] = field(default_factory=list)

    @asynccontextmanager
    async def measure(self) -> AsyncIterator[None]:
        """async-context-manager: измеряет blocking elapsed внутри блока."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.samples.append(time.perf_counter() - t0)

    def record(self, elapsed_seconds: float) -> None:
        """Прямая запись готового замера (для случаев, где
        ``async with`` неудобен)."""
        self.samples.append(elapsed_seconds)

    def report(self, label: str) -> None:
        """Печатает форматированный latency-отчёт. Все числа в мс
        для удобства соотнесения с production-budget'ами."""
        if not self.samples:
            print(f"\n[latency] {label}: НЕТ замеров")
            return
        s = sorted(self.samples)
        mean_ms = statistics.mean(s) * 1000
        p50_ms = _percentile(s, 0.50) * 1000
        p95_ms = _percentile(s, 0.95) * 1000
        p99_ms = _percentile(s, 0.99) * 1000
        max_ms = s[-1] * 1000

        # Двойной перенос строки в начале — отделяет отчёт от точки pytest'а.
        print(
            "\n"
            "─" * 60 + "\n"
            f"  latency report · {label}\n"
            "─" * 60 + "\n"
            f"  samples : {len(s)}\n"
            f"  mean    : {mean_ms:7.2f} ms\n"
            f"  p50     : {p50_ms:7.2f} ms\n"
            f"  p95     : {p95_ms:7.2f} ms\n"
            f"  p99     : {p99_ms:7.2f} ms\n"
            f"  max     : {max_ms:7.2f} ms\n"
            + "─" * 60
        )


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(q * len(sorted_values))))
    return sorted_values[idx]


@pytest.fixture
def latency() -> LatencyTracker:
    """Per-test latency tracker — новая инстанция на каждый тест,
    чтобы замеры не накапливались между сценариями."""
    return LatencyTracker()
