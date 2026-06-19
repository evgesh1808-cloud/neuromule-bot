"""PR-O: тесты на DB-индексы (hot queries) + timing-обёртку.

Тестовая стратегия:

* Открываем временную SQLite БД (in-file ради WAL-mode);
* Накатываем `init_db()` целиком — это идеомпотентно;
* Проверяем через ``EXPLAIN QUERY PLAN``, что hot queries
  используют индексы (а не SCAN TABLE);
* Проверяем, что повторный ``ensure_pr_o_indexes`` не падает
  (идемпотентность);
* Проверяем ``TimedQuery``: метрика ``db.query_ms{name}`` пишется
  с положительной длительностью, и пишется даже при exception'е.
"""
from __future__ import annotations

import os
import time

import pytest

from services import metrics
from services.db_timing import TimedQuery


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


# ───────────────────────────────────────────────────────────────────────
# 1. Индексы PR-O живут в схеме после init_db
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    """Свежая SQLite БД для каждого теста. Полностью изолированная."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("MAIN_DB", str(db_file))

    # Очищаем кеш модуля repository, чтобы DB_PATH перечитался.
    import importlib

    from services import repository as repo

    importlib.reload(repo)
    await repo.init_db("")
    yield repo
    # Сборка мусора — БД-файл удалится вместе с tmp_path.


@pytest.mark.asyncio
async def test_pr_o_indexes_exist_after_init_db(fresh_db) -> None:
    import aiosqlite

    repo = fresh_db
    async with aiosqlite.connect(repo.DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_pro_%' ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()

    names = {row[0] for row in rows}
    assert names == {
        "idx_pro_referrals_inviter_id",
        "idx_pro_payment_events_user_created",
        "idx_pro_payment_events_created_at",
        "idx_pro_payment_charges_user_id",
        "idx_pro_users_referred_by",
    }


# ───────────────────────────────────────────────────────────────────────
# 2. EXPLAIN QUERY PLAN — hot queries используют индексы
# ───────────────────────────────────────────────────────────────────────


async def _explain(db, sql: str, params: tuple) -> str:
    """Возвращает плоский текст плана запроса (одной строкой)."""
    async with db.execute(f"EXPLAIN QUERY PLAN {sql}", params) as cur:
        rows = await cur.fetchall()
    return " | ".join(" ".join(str(x) for x in row) for row in rows)


@pytest.mark.asyncio
async def test_referrals_count_uses_inviter_index(fresh_db) -> None:
    import aiosqlite

    repo = fresh_db
    async with aiosqlite.connect(repo.DB_PATH) as db:
        plan = await _explain(
            db, "SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (1,)
        )

    # SQLite должен использовать idx_pro_referrals_inviter_id, а не SCAN.
    assert "idx_pro_referrals_inviter_id" in plan or "USING INDEX" in plan
    assert "SCAN referrals" not in plan or "USING INDEX" in plan


@pytest.mark.asyncio
async def test_payment_events_by_user_uses_composite_index(fresh_db) -> None:
    import aiosqlite

    repo = fresh_db
    async with aiosqlite.connect(repo.DB_PATH) as db:
        plan = await _explain(
            db,
            "SELECT id FROM payment_events WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (42,),
        )

    assert "idx_pro_payment_events_user_created" in plan


@pytest.mark.asyncio
async def test_payment_events_by_date_uses_created_at_index(fresh_db) -> None:
    import aiosqlite

    repo = fresh_db
    async with aiosqlite.connect(repo.DB_PATH) as db:
        plan = await _explain(
            db,
            "SELECT tariff, COUNT(*) FROM payment_events "
            "WHERE created_at = ? GROUP BY tariff",
            ("2026-05-26",),
        )

    # Может быть либо `created_at`, либо composite — SQLite сам выберет.
    assert (
        "idx_pro_payment_events_created_at" in plan
        or "idx_pro_payment_events_user_created" in plan
    )


@pytest.mark.asyncio
async def test_payment_charges_by_user_uses_index(fresh_db) -> None:
    import aiosqlite

    repo = fresh_db
    async with aiosqlite.connect(repo.DB_PATH) as db:
        plan = await _explain(
            db,
            "SELECT charge_id FROM payment_charges WHERE user_id = ?",
            (777,),
        )

    assert "idx_pro_payment_charges_user_id" in plan


@pytest.mark.asyncio
async def test_users_referred_by_uses_partial_index(fresh_db) -> None:
    """Partial index на ``users(referred_by) WHERE referred_by IS NOT NULL``
    должен подхватываться запросом «найти всех, кого пригласил X»."""
    import aiosqlite

    repo = fresh_db
    async with aiosqlite.connect(repo.DB_PATH) as db:
        plan = await _explain(
            db,
            "SELECT id FROM users WHERE referred_by = ?",
            (42,),
        )

    assert "idx_pro_users_referred_by" in plan


# ───────────────────────────────────────────────────────────────────────
# 3. ensure_pr_o_indexes идемпотентен
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_is_idempotent(fresh_db) -> None:
    """Повторный запуск ensure_pr_o_indexes не должен падать
    (важно для rolling-deploy с двумя репликами на одной БД)."""

    import aiosqlite

    from services.db_indexes import ensure_pr_o_indexes

    repo = fresh_db
    async with aiosqlite.connect(repo.DB_PATH) as db:
        await ensure_pr_o_indexes(db)
        await ensure_pr_o_indexes(db)  # второй раз — должно быть no-op
        await db.commit()


# ───────────────────────────────────────────────────────────────────────
# 4. TimedQuery — метрики
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timed_query_records_metric_on_success() -> None:
    async with TimedQuery("my_query"):
        await _async_sleep_ms(5)

    snap = metrics.snapshot()
    hist = snap["histograms"]["db.query_ms{name=my_query}"]
    assert hist["count"] == 1
    # Длительность ≥ 5ms (с большим зазором на CI-jitter).
    assert hist["max"] >= 4.0


@pytest.mark.asyncio
async def test_timed_query_records_metric_on_exception() -> None:
    """Метрика пишется даже если внутри блока было исключение."""
    with pytest.raises(RuntimeError, match="boom"):
        async with TimedQuery("my_query_with_error"):
            await _async_sleep_ms(3)
            raise RuntimeError("boom")

    snap = metrics.snapshot()
    hist = snap["histograms"]["db.query_ms{name=my_query_with_error}"]
    assert hist["count"] == 1
    assert hist["max"] >= 2.0


@pytest.mark.asyncio
async def test_timed_query_multiple_calls_aggregate() -> None:
    for _ in range(3):
        async with TimedQuery("aggregated_query"):
            await _async_sleep_ms(2)

    snap = metrics.snapshot()
    hist = snap["histograms"]["db.query_ms{name=aggregated_query}"]
    assert hist["count"] == 3
    assert hist["sum"] > 0


async def _async_sleep_ms(ms: int) -> None:
    """Точный микросон без asyncio.sleep (тот огрубляет до 15ms на Windows)."""
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        pass  # busy-wait — детерминирован под любой ОС
