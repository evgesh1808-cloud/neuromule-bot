"""Конкурентные / нагрузочные integration-тесты PR-P · Phase 1a.

Что проверяем:

* **Контрактная гарантия идемпотентности под гонкой.** N параллельных
  корутин делают ``claim_payment_charge`` с одним ``charge_id`` —
  ровно ОДНА должна получить ``True``, остальные ``False``.
  Опирается на PRIMARY KEY + ``ON CONFLICT DO NOTHING``.
* Парный сценарий с **разными** charge_id — никаких ложных конфликтов.
* Latency-репортинг в pytest-выводе (p50 / p95 / p99 / max) —
  фиксируется на каждый concurrent-сценарий через фикстуру
  ``latency`` (см. ``tests/integration/conftest.py``).

Запуск с просмотром отчётов:

    pytest tests/integration/test_pg_load.py -v -s -m integration

Без ``-s`` отчёты прячутся capture'ом stdout; они всё равно появятся
в diagnostics при падении теста.

Полноценный benchmark с CLI / exit-кодами — ``tools/loadtest_pg.py``.
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


# ── Гонка: один charge_id, 30 корутин ────────────────────────────────────


async def test_concurrent_same_charge_id_only_one_wins(
    pg_pool, clean_pg, latency
) -> None:
    """30 параллельных claim'ов с одним ``charge_id`` →
    ровно 1 ``True``, 29 ``False``. БД содержит ровно 1 строку.

    Это контракт идемпотентности — основа всей миграции. Если упал,
    значит регрессия в SQL ``claim_payment_charge`` или в UNIQUE-
    constraint'е на ``telegram_payment_charge_id``."""

    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(40001)

    charge_id = "ch_race_001"

    async def one_attempt() -> bool:
        async with latency.measure():
            async with db_transaction(pg_pool) as conn:
                return await PaymentRepository(conn).claim_payment_charge(
                    charge_id, 40001, pack_index=1
                )

    results = await asyncio.gather(*(one_attempt() for _ in range(30)))
    latency.report("race · 30 corotines, same charge_id")

    winners = [r for r in results if r is True]
    losers = [r for r in results if r is False]
    assert len(winners) == 1, (
        f"ON CONFLICT нарушен: {len(winners)} winners вместо 1"
    )
    assert len(losers) == 29

    async with pg_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM payment_charges "
            "WHERE telegram_payment_charge_id = $1",
            charge_id,
        )
    assert cnt == 1


# ── Гонка: разные charge_id — все должны быть True ──────────────────────


async def test_concurrent_different_charge_ids_all_win(
    pg_pool, clean_pg, latency
) -> None:
    """50 параллельных claim'ов с РАЗНЫМИ ``charge_id`` →
    все 50 получают ``True``. Защита от ложных конфликтов
    (например, если бы UNIQUE случайно повесили на user_id)."""

    user_id = 40002
    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(user_id)

    async def one_attempt(i: int) -> bool:
        async with latency.measure():
            async with db_transaction(pg_pool) as conn:
                return await PaymentRepository(conn).claim_payment_charge(
                    f"ch_uniq_{i:03d}", user_id, pack_index=i % 5
                )

    results = await asyncio.gather(*(one_attempt(i) for i in range(50)))
    latency.report("unique · 50 corotines, distinct charge_ids")

    assert all(r is True for r in results), (
        f"ожидаем 50 winners, получили {sum(results)}"
    )
    async with pg_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM payment_charges WHERE user_id = $1",
            user_id,
        )
    assert cnt == 50


# ── Микро-нагрузочный smoke (200 claim'ов, 20 в параллель) ──────────────


async def test_load_smoke_200_claims_finish_under_pool_limits(
    pg_pool, clean_pg, latency
) -> None:
    """200 claim'ов с лимитом 20 параллельных. Цель:
      1) пул не голодает (min=10, max=50);
      2) ни один claim не падает с TimeoutError;
      3) все ``True`` (charge_id уникальны).

    Бюджеты для прод-машины (см. PHASE1A_TEST_PLAN §4.1):
    p50 ≤ 5ms, p95 ≤ 20ms, p99 ≤ 50ms. Здесь только smoke —
    жёстких assertion'ов на latency нет (флапы CI на маленькой
    железке). Полноценный gate'инг — на ``tools/loadtest_pg.py``."""

    user_id = 40003
    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(user_id)

    sem = asyncio.Semaphore(20)

    async def one(i: int) -> bool:
        async with sem:
            async with latency.measure():
                async with db_transaction(pg_pool) as conn:
                    return await PaymentRepository(conn).claim_payment_charge(
                        f"ch_load_{i:04d}", user_id, pack_index=i % 7
                    )

    results = await asyncio.gather(*(one(i) for i in range(200)))
    latency.report("smoke · 200 claims, concurrency=20")

    assert sum(results) == 200, "ожидаем 200 успешных claim'ов"
    async with pg_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM payment_charges WHERE user_id = $1",
            user_id,
        )
    assert cnt == 200
