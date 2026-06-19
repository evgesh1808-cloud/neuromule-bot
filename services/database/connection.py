"""PostgreSQL connection pool + транзакционный context manager.

Production-инварианты (см. ``.cursorrules`` §1.9 / PR-P):

* ``command_timeout=5.0`` — любой запрос > 5 с убивается клиентом
  (защита от runaway-запросов, забивающих пул до max_size);
* ``timeout=5.0`` — таймаут на acquire connection из пула и на
  установление нового TCP/TLS-соединения с PG (≈ connect_timeout);
* ``min_size=10, max_size=50`` — bot-профиль высокой нагрузки;
* ``statement_timeout=5000ms`` на server-side — дублирующая защита;
* ``jit=off`` — JIT добавляет ~20–50 мс на короткие запросы bot'а,
  у нас этого latency-бюджета нет;
* ``application_name='neuromule_bot'`` — для observability в
  ``pg_stat_activity``.

Транзакционный паттерн (``db_transaction``):

* открывает connection из пула → стартует ``BEGIN`` →
  yield ``Connection``;
* любой ``Exception`` → ROLLBACK + ``logger.exception(...)`` + re-raise
  (call-site сам решает, что делать с ошибкой);
* успешный выход → COMMIT.

Используется как:

    async with db_transaction(pool) as conn:
        repo = PaymentRepository(conn)
        await repo.claim_payment_charge(...)

ВСЕ generic ``except Exception`` строго ограничены этой функцией —
это последняя точка перехвата перед re-raise. Конкретные обработчики
(``asyncpg.PostgresError``) пишут call-site'ы при необходимости.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Final

import asyncpg

if TYPE_CHECKING:
    from asyncpg import Connection, Pool

logger = logging.getLogger(__name__)


# ── Production-safe defaults ─────────────────────────────────────────────

POOL_MIN_SIZE: Final[int] = 10
POOL_MAX_SIZE: Final[int] = 50
COMMAND_TIMEOUT_SEC: Final[float] = 5.0
CONNECT_TIMEOUT_SEC: Final[float] = 5.0
STATEMENT_TIMEOUT_MS: Final[int] = 5000
INACTIVE_LIFETIME_SEC: Final[int] = 300


async def _init_connection(conn: "Connection") -> None:
    """Server-side инварианты для КАЖДОГО коннекта в пуле."""
    await conn.execute("SET TIME ZONE 'UTC'")
    await conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
    await conn.execute("SET jit = off")
    await conn.execute("SET application_name = 'neuromule_bot'")


async def init_postgres_pool(dsn: str) -> "Pool":
    """Создать и прогреть asyncpg-пул со строгими таймаутами безопасности.

    Args:
        dsn: ``postgresql://user:pass@host:5432/db`` (опц. ``?sslmode=require``).

    Returns:
        Прогретый ``asyncpg.Pool`` (минимум ``POOL_MIN_SIZE`` коннектов
        уже открыты и инициализированы через ``_init_connection``).

    Raises:
        ValueError: если ``dsn`` пуст.
        asyncpg.PostgresError: если PG недоступен в течение ``CONNECT_TIMEOUT_SEC``.
    """

    if not dsn:
        raise ValueError("init_postgres_pool: DSN must be non-empty")

    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        command_timeout=COMMAND_TIMEOUT_SEC,
        timeout=CONNECT_TIMEOUT_SEC,
        max_inactive_connection_lifetime=INACTIVE_LIFETIME_SEC,
        init=_init_connection,
    )
    logger.info(
        "postgres pool ready: min=%s max=%s cmd_timeout=%ss connect_timeout=%ss",
        POOL_MIN_SIZE,
        POOL_MAX_SIZE,
        COMMAND_TIMEOUT_SEC,
        CONNECT_TIMEOUT_SEC,
    )
    return pool


@asynccontextmanager
async def db_transaction(pool: "Pool") -> AsyncIterator["Connection"]:
    """Открыть connection + транзакцию с безопасным rollback на исключении.

    Семантика:

    * Успешный выход из блока → ``COMMIT``.
    * Любое исключение внутри блока → ``logger.exception`` + ``ROLLBACK`` +
      re-raise. Call-site видит исходное исключение и решает,
      что делать дальше (вернуть юзеру ошибку, retry, etc.).

    Connection возвращается в пул автоматически по выходу из
    ``async with pool.acquire()``.

    Example:
        async with db_transaction(pool) as conn:
            repo = PaymentRepository(conn)
            is_new = await repo.claim_payment_charge(charge_id, uid, pkg)
            if is_new:
                await _credit_pack(conn, uid, pkg)  # тот же conn = атомарно
    """

    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                yield conn
        except Exception:
            # ``async with conn.transaction()`` уже сделал ROLLBACK при
            # выходе с исключением. Здесь мы только логируем причину —
            # это последняя точка с полным контекстом call-stack'а.
            logger.exception(
                "db_transaction: rolled back due to error"
            )
            raise


__all__ = (
    "COMMAND_TIMEOUT_SEC",
    "CONNECT_TIMEOUT_SEC",
    "POOL_MAX_SIZE",
    "POOL_MIN_SIZE",
    "db_transaction",
    "init_postgres_pool",
)
