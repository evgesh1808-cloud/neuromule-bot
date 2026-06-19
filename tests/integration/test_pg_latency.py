"""Целевой latency-benchmark из pytest (PR-P · Phase 1a).

Отличается от ``test_pg_load.py`` тем, что:

* **Меряет конкретные production-операции** изолированно
  (один claim, один `is_tos_accepted`, ROUND-TRIP `SELECT 1`).
* **Имеет мягкие gate-assert'ы** на p99 — флапы на слабых машинах
  не должны валить весь suite, поэтому пороги либеральные.
  Жёсткие пороги — в ``tools/loadtest_pg.py``.
* **Печатает все метрики** даже на pass — для baseline-трекинга
  между релизами (запускайте раз в спринт и сохраняйте отчёты).

Запуск:

    POSTGRES_TEST_DSN=postgresql://test:test@127.0.0.1:55432/neuromule_test \
        pytest tests/integration/test_pg_latency.py -v -s -m integration

Ожидаемые пороги на dev-машине с локальным Docker-PG (alpine, host
NVMe SSD, AMD/Intel современный CPU):

| Операция          | p50     | p99    | gate (p99 max) |
| ----------------- | ------- | ------ | -------------- |
| ROUND-TRIP `SELECT 1` | < 1 ms  | < 5 ms | 50 ms          |
| `is_tos_accepted` | < 2 ms  | < 10 ms| 100 ms         |
| `claim_payment_charge` (cold) | < 5 ms  | < 20 ms| 150 ms         |
| `claim_payment_charge` (warm pool) | < 3 ms  | < 15 ms| 100 ms         |

Gate'ы намеренно в ~7-10x от ожидаемого, чтобы CI на medium-runner'е
не флапал. Реальные регрессии (раз в 5-10) — увидим в отчёте сразу.
"""
from __future__ import annotations

import asyncio

import pytest

from services.database import (
    PaymentRepository,
    UserRepository,
    db_transaction,
)


pytestmark = pytest.mark.integration


# Гейты p99 (миллисекунды) — мягкие, чтобы не флапать на слабом CI.
_GATE_SELECT_1_P99_MS = 50.0
_GATE_TOS_P99_MS = 100.0
_GATE_CLAIM_COLD_P99_MS = 150.0
_GATE_CLAIM_WARM_P99_MS = 100.0


def _p(samples: list[float], q: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    idx = max(0, min(len(s) - 1, int(q * len(s))))
    return s[idx] * 1000.0


# ── ROUND-TRIP latency (sanity baseline) ────────────────────────────────


async def test_baseline_select_one_latency(pg_pool, latency) -> None:
    """Базовая network + pool overhead. Если уже здесь p99 > 50 ms,
    дальнейшие замеры бессмысленны — копать в сеть/Docker/VPS."""

    async with pg_pool.acquire() as conn:  # warm-up
        await conn.fetchval("SELECT 1")

    for _ in range(200):
        async with latency.measure():
            async with pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

    latency.report("baseline · SELECT 1 (acquire+release, 200 iter)")
    p99 = _p(latency.samples, 0.99)
    assert p99 < _GATE_SELECT_1_P99_MS, (
        f"baseline SELECT 1 p99={p99:.1f}ms > {_GATE_SELECT_1_P99_MS} ms — "
        "проверьте сеть до PG и состояние Docker-демона"
    )


# ── is_tos_accepted (hot path TosGateMiddleware) ────────────────────────


async def test_is_tos_accepted_latency(pg_pool, clean_pg, latency) -> None:
    """``is_tos_accepted`` — вызывается из ``TosGateMiddleware`` на КАЖДОМ
    update'е. Это самая горячая read-операция в боте."""

    async with db_transaction(pg_pool) as conn:
        repo = UserRepository(conn)
        for uid in range(50000, 50100):
            await repo.accept_tos(uid)

    user_ids = list(range(50000, 50100)) * 2  # 200 iter
    for uid in user_ids:
        async with latency.measure():
            async with pg_pool.acquire() as conn:
                await UserRepository(conn).is_tos_accepted(uid)

    latency.report("hot · is_tos_accepted (200 iter)")
    p99 = _p(latency.samples, 0.99)
    assert p99 < _GATE_TOS_P99_MS, (
        f"is_tos_accepted p99={p99:.1f}ms > {_GATE_TOS_P99_MS} ms"
    )


# ── claim_payment_charge — cold (новый юзер, без warm pool) ─────────────


async def test_claim_payment_charge_cold_latency(
    pg_pool, clean_pg, latency
) -> None:
    """100 уникальных claim'ов без предварительного прогрева пула.
    Первые несколько замеров будут включать acquire + connect overhead
    на свежих коннектах из ``min_size=10..max_size=50``."""

    user_id = 60001
    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(user_id)

    for i in range(100):
        async with latency.measure():
            async with db_transaction(pg_pool) as conn:
                await PaymentRepository(conn).claim_payment_charge(
                    f"ch_cold_{i:04d}", user_id, pack_index=i % 7
                )

    latency.report("cold · claim_payment_charge (100 sequential iter)")
    p99 = _p(latency.samples, 0.99)
    assert p99 < _GATE_CLAIM_COLD_P99_MS, (
        f"cold claim p99={p99:.1f}ms > {_GATE_CLAIM_COLD_P99_MS} ms"
    )


# ── claim_payment_charge — warm pool (production-realistic) ─────────────


async def test_claim_payment_charge_warm_latency(
    pg_pool, clean_pg, latency
) -> None:
    """500 claim'ов с прогретым пулом и concurrency=10 — это
    наиболее близкий к production сценарий
    (пул горячий, transactions параллельно)."""

    user_id = 60002
    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(user_id)

    # warm-up: 50 dummy SELECT'ов чтобы наполнить пул активными коннектами.
    async def warmup_one() -> None:
        async with pg_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    await asyncio.gather(*(warmup_one() for _ in range(50)))

    sem = asyncio.Semaphore(10)

    async def one(i: int) -> None:
        async with sem:
            async with latency.measure():
                async with db_transaction(pg_pool) as conn:
                    await PaymentRepository(conn).claim_payment_charge(
                        f"ch_warm_{i:05d}", user_id, pack_index=i % 7
                    )

    await asyncio.gather(*(one(i) for i in range(500)))

    latency.report("warm · claim_payment_charge (500 iter, conc=10)")
    p99 = _p(latency.samples, 0.99)
    assert p99 < _GATE_CLAIM_WARM_P99_MS, (
        f"warm claim p99={p99:.1f}ms > {_GATE_CLAIM_WARM_P99_MS} ms — "
        "проверьте `command_timeout` и нагрузку на PG-инстанс"
    )

    # Контрольная сверка — все 500 строк должны быть в БД.
    async with pg_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM payment_charges WHERE user_id = $1",
            user_id,
        )
    assert cnt == 500
