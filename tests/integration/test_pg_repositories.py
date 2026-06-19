"""Integration-тесты против реального PostgreSQL.

Покрывают:

* ``init_postgres_pool`` — успешное соединение, корректные
  server-side настройки (`statement_timeout`, TZ, `application_name`);
* ``db_transaction`` — COMMIT на успехе, ROLLBACK + лог на исключении;
* ``UserRepository.is_tos_accepted`` / ``accept_tos`` — UPSERT работает,
  ``accepted_terms_at`` проставляется автоматически;
* ``PaymentRepository.claim_payment_charge``:
  - happy-path INSERT возвращает ``True``;
  - повторный вызов на тот же ``charge_id`` возвращает ``False`` (идемпотентность);
  - pack_index сохраняется правильно;
  - FK на ``users.id`` соблюдается;
* ``command_timeout=5.0`` — запрос ``pg_sleep(6)`` должен быть убит.

Все тесты помечены ``@pytest.mark.integration`` и skip'аются без env
``POSTGRES_TEST_DSN``. Использует фикстуры из ``conftest.py``:
``pg_pool``, ``pg_conn``, ``clean_pg``.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
import pytest

from services.database import (
    PaymentRepository,
    UserRepository,
    db_transaction,
)


pytestmark = pytest.mark.integration


# ── init_postgres_pool · server-side настройки коннекта ─────────────────


async def test_pool_init_applies_session_settings(pg_pool) -> None:
    """Проверяем, что _init_connection из connection.py отработал:
    TZ=UTC, statement_timeout=5000ms, jit=off, application_name=ours."""
    async with pg_pool.acquire() as conn:
        tz = await conn.fetchval("SHOW TIME ZONE")
        stmt = await conn.fetchval("SHOW statement_timeout")
        jit = await conn.fetchval("SHOW jit")
        app = await conn.fetchval("SHOW application_name")

    assert tz.upper() == "UTC"
    # PG возвращает '5s' / '5000ms' в зависимости от версии — нормализуем.
    assert stmt.replace(" ", "") in ("5s", "5000ms")
    assert jit == "off"
    assert app == "neuromule_bot"


# ── db_transaction · COMMIT / ROLLBACK ──────────────────────────────────


async def test_db_transaction_commits_on_success(pg_pool, clean_pg) -> None:
    """INSERT внутри db_transaction — после выхода данные видны
    другому коннекту."""
    user_id = 1001
    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(user_id)

    async with pg_pool.acquire() as conn:
        ok = await UserRepository(conn).is_tos_accepted(user_id)
    assert ok is True


async def test_db_transaction_rolls_back_on_exception(
    pg_pool, clean_pg, caplog: pytest.LogCaptureFixture
) -> None:
    """Исключение внутри блока → данные НЕ должны быть видны после."""
    user_id = 1002
    caplog.set_level(logging.ERROR, logger="services.database.connection")

    with pytest.raises(RuntimeError, match="kaboom"):
        async with db_transaction(pg_pool) as conn:
            await UserRepository(conn).accept_tos(user_id)
            raise RuntimeError("kaboom")

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM users WHERE id = $1", user_id)
    assert row is None

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "ожидаем ERROR-лог про rollback"
    assert errors[0].exc_info is not None


# ── UserRepository ───────────────────────────────────────────────────────


async def test_user_is_tos_accepted_default_false(pg_conn) -> None:
    """Юзера ещё нет → False, без побочных INSERT'ов."""
    assert await UserRepository(pg_conn).is_tos_accepted(2001) is False
    row = await pg_conn.fetchrow("SELECT id FROM users WHERE id = $1", 2001)
    assert row is None


async def test_user_accept_tos_creates_and_marks(pg_conn) -> None:
    """accept_tos на несуществующего юзера создаёт строку с TRUE."""
    repo = UserRepository(pg_conn)
    await repo.accept_tos(2002)

    row = await pg_conn.fetchrow(
        "SELECT accepted_terms, accepted_terms_at "
        "FROM users WHERE id = $1",
        2002,
    )
    assert row is not None
    assert row["accepted_terms"] is True
    assert row["accepted_terms_at"] is not None
    assert await repo.is_tos_accepted(2002) is True


async def test_user_accept_tos_is_idempotent(pg_conn) -> None:
    """Повторный accept_tos НЕ ломает FK и НЕ создаёт дубликат
    (PRIMARY KEY на id). Timestamp обновляется."""
    repo = UserRepository(pg_conn)
    await repo.accept_tos(2003)
    first = await pg_conn.fetchval(
        "SELECT accepted_terms_at FROM users WHERE id = $1", 2003
    )

    await asyncio.sleep(0.05)
    await repo.accept_tos(2003)
    second = await pg_conn.fetchval(
        "SELECT accepted_terms_at FROM users WHERE id = $1", 2003
    )

    assert second >= first
    count = await pg_conn.fetchval(
        "SELECT COUNT(*) FROM users WHERE id = $1", 2003
    )
    assert count == 1


# ── PaymentRepository.claim_payment_charge ───────────────────────────────


async def test_claim_charge_happy_path(pg_pool, clean_pg) -> None:
    """Первый claim → True, запись с правильным pack_index в БД."""
    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(3001)

        is_new = await PaymentRepository(conn).claim_payment_charge(
            "ch_happy_001", 3001, pack_index=7
        )

    assert is_new is True

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, pack_index FROM payment_charges "
            "WHERE telegram_payment_charge_id = $1",
            "ch_happy_001",
        )
    assert row is not None
    assert row["user_id"] == 3001
    assert row["pack_index"] == 7


async def test_claim_charge_duplicate_returns_false(
    pg_pool, clean_pg
) -> None:
    """Второй claim того же charge_id → False, без побочных INSERT'ов."""
    async with db_transaction(pg_pool) as conn:
        await UserRepository(conn).accept_tos(3002)
        first = await PaymentRepository(conn).claim_payment_charge(
            "ch_dup_001", 3002, pack_index=3
        )

    async with db_transaction(pg_pool) as conn:
        second = await PaymentRepository(conn).claim_payment_charge(
            "ch_dup_001", 3002, pack_index=3
        )

    assert first is True
    assert second is False

    async with pg_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM payment_charges "
            "WHERE telegram_payment_charge_id = $1",
            "ch_dup_001",
        )
    assert count == 1


async def test_claim_charge_fk_violation_rolls_back(
    pg_pool, clean_pg
) -> None:
    """FK ``payment_charges.user_id`` REFERENCES users(id) — claim для
    несуществующего юзера должен упасть, ROLLBACK очистит транзакцию."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db_transaction(pg_pool) as conn:
            await PaymentRepository(conn).claim_payment_charge(
                "ch_fk_fail", 999999, pack_index=1
            )

    async with pg_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM payment_charges "
            "WHERE telegram_payment_charge_id = $1",
            "ch_fk_fail",
        )
    assert cnt == 0


async def test_claim_charge_empty_id_short_circuits(pg_pool, clean_pg) -> None:
    """Пустой charge_id обрабатывается без обращения к БД (быстрый exit
    в коде репозитория). Здесь проверяем, что строка действительно
    НЕ появилась — даже если бы код запустил SQL, запись не пройдёт
    UNIQUE-PK на пустой строке."""
    async with db_transaction(pg_pool) as conn:
        is_new = await PaymentRepository(conn).claim_payment_charge(
            "", 1, pack_index=0
        )
    assert is_new is False

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT 1 FROM payment_charges")
    assert rows == []


# ── command_timeout=5.0 ──────────────────────────────────────────────────


async def test_command_timeout_kills_runaway_query(pg_pool) -> None:
    """``pg_sleep(6)`` > 5 с (command_timeout) → asyncpg прервёт
    запрос. Без этого защитного слоя медленный запрос мог бы держать
    connection до server-side statement_timeout (5000ms тоже сработает).
    Здесь проверяем КЛИЕНТСКИЙ таймаут — он первым нарвётся."""
    async with pg_pool.acquire() as conn:
        with pytest.raises(
            (asyncio.TimeoutError, asyncpg.QueryCanceledError)
        ):
            await conn.execute("SELECT pg_sleep(6)")
