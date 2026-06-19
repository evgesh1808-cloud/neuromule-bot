"""Standalone нагрузочный тест PG-слоя PR-P (Phase 1a).

Запуск:

    POSTGRES_TEST_DSN=postgresql://test:test@127.0.0.1:55432/neuromule_test \
        python tools/loadtest_pg.py --total 5000 --concurrency 50

Что меряем:

1. **Throughput** (claim'ов в секунду) — реальная пропускная способность
   ``PaymentRepository.claim_payment_charge`` на пуле ``10..50``.
2. **Latency** на claim (p50, p95, p99, max) — попадаем ли в наш
   бюджет 5 с (``command_timeout``).
3. **Доля duplicate'ов** при ``--race`` сценарии (один charge_id для
   всех клиентов) — должна быть ``total - 1``.
4. **Корректность ON CONFLICT под гонкой** — счётчик INSERT'ов в БД
   должен сойтись с числом winners.

CLI:

    --total N           — общее число claim'ов (default 1000)
    --concurrency C     — одновременных корутин (default 20)
    --race              — все клиенты используют ОДИН charge_id (гонка)
    --dsn DSN           — явный DSN (иначе берём из POSTGRES_TEST_DSN)

Безопасность:

* Скрипт **не предназначен для production**. Перед запуском убедитесь,
  что DSN указывает на ТЕСТОВУЮ базу (см. ``docker-compose.test.yml``).
* Тестовый user_id = 0, перед стартом он `accept_tos`-ится.
* После завершения скрипт оставляет данные — для аналитики через
  ``psql``. Сносить через ``TRUNCATE`` или ``docker compose down -v``.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import statistics
import sys
import time
from pathlib import Path

# Гарантируем импорт `services.database` из любого CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from services.database import (  # noqa: E402
    PaymentRepository,
    UserRepository,
    db_transaction,
    init_postgres_pool,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("loadtest_pg")


# ── одиночный claim с замером latency ───────────────────────────────────


async def _one_claim(pool, charge_id: str, user_id: int, pack: int) -> tuple[bool, float]:
    """Возвращает (is_new, elapsed_seconds)."""
    t0 = time.perf_counter()
    async with db_transaction(pool) as conn:
        ok = await PaymentRepository(conn).claim_payment_charge(
            charge_id, user_id, pack
        )
    return ok, time.perf_counter() - t0


# ── основной цикл ──────────────────────────────────────────────────────


async def run_loadtest(
    dsn: str, total: int, concurrency: int, race: bool
) -> int:
    pool = await init_postgres_pool(dsn)
    logger.info(
        "loadtest: total=%d concurrency=%d race=%s",
        total, concurrency, race,
    )
    user_id = 0  # фиксированный test-user; не пересекается с реальными
    async with db_transaction(pool) as conn:
        await UserRepository(conn).accept_tos(user_id)

    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    winners = 0
    losers = 0
    errors = 0
    err_samples: list[str] = []

    async def task(i: int) -> None:
        nonlocal winners, losers, errors
        charge_id = "ch_race" if race else f"ch_load_{i:07d}"
        pack = i % 7
        async with sem:
            try:
                ok, dt = await _one_claim(pool, charge_id, user_id, pack)
            except Exception as exc:  # noqa: BLE001 — benchmark, ловим всё
                errors += 1
                if len(err_samples) < 5:
                    err_samples.append(f"{type(exc).__name__}: {exc}")
                return
            latencies.append(dt)
            if ok:
                winners += 1
            else:
                losers += 1

    t_start = time.perf_counter()
    await asyncio.gather(*(task(i) for i in range(total)))
    wall = time.perf_counter() - t_start

    # ── контрольная сверка ────────────────────────────────────────────
    async with pool.acquire() as conn:
        if race:
            rows = await conn.fetchval(
                "SELECT COUNT(*) FROM payment_charges "
                "WHERE telegram_payment_charge_id = 'ch_race'"
            )
            expected_rows = 1 if winners > 0 else 0
        else:
            rows = await conn.fetchval(
                "SELECT COUNT(*) FROM payment_charges "
                "WHERE user_id = $1 AND telegram_payment_charge_id LIKE 'ch_load_%'",
                user_id,
            )
            expected_rows = winners

    await pool.close()

    # ── отчёт ─────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f" PR-P Phase 1a · PG loadtest report")
    print("=" * 60)
    print(f"  total       : {total}")
    print(f"  concurrency : {concurrency}")
    print(f"  mode        : {'RACE (один charge_id)' if race else 'unique charge_ids'}")
    print(f"  wall time   : {wall:.2f} s")
    print(f"  throughput  : {total / wall:.1f} claims/s")
    print()
    print(f"  winners     : {winners}")
    print(f"  losers      : {losers}")
    print(f"  errors      : {errors}")
    if err_samples:
        for s in err_samples:
            print(f"     ! {s}")
    print()
    if latencies:
        latencies.sort()
        p50 = _percentile(latencies, 0.50)
        p95 = _percentile(latencies, 0.95)
        p99 = _percentile(latencies, 0.99)
        print(f"  latency (s) :")
        print(f"     mean : {statistics.mean(latencies):.4f}")
        print(f"     p50  : {p50:.4f}")
        print(f"     p95  : {p95:.4f}")
        print(f"     p99  : {p99:.4f}")
        print(f"     max  : {latencies[-1]:.4f}")
    print()
    print(f"  DB rows     : {rows}  (expected {expected_rows})")
    consistent = (rows == expected_rows)
    print(f"  consistent  : {'YES' if consistent else 'NO  <-- FAIL'}")
    print("=" * 60)

    if errors > 0:
        return 2
    if not consistent:
        return 3
    if race and winners != 1:
        print(f"RACE expected exactly 1 winner, got {winners}")
        return 4
    return 0


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(q * len(sorted_values))))
    return sorted_values[idx]


def _resolve_dsn(args: argparse.Namespace) -> str:
    if args.dsn:
        return args.dsn
    dsn = os.environ.get("POSTGRES_TEST_DSN", "").strip()
    if dsn:
        return dsn
    raise SystemExit(
        "DSN не задан: используйте --dsn или установите POSTGRES_TEST_DSN"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="PR-P PG loadtest")
    ap.add_argument("--total", type=int, default=1000)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--race", action="store_true",
                    help="один charge_id для всех (тест ON CONFLICT)")
    ap.add_argument("--dsn", type=str, default="")
    args = ap.parse_args()

    dsn = _resolve_dsn(args)
    code = asyncio.run(
        run_loadtest(dsn, args.total, args.concurrency, args.race)
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
