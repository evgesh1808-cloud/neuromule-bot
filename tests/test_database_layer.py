"""Тесты на ``services.database`` (connection + repositories).

Все асинхронные операции мокаются через ``AsyncMock`` — реальный
PostgreSQL для прогона не требуется. Это unit-уровень: проверяем
поведение слоя относительно ``asyncpg.Connection``-контракта, не
сам asyncpg.

Что покрыто:

* ``init_postgres_pool`` передаёт правильные таймауты и размеры пула;
* ``init_postgres_pool("")`` → ``ValueError``;
* ``db_transaction`` commit'ит на успехе и rollback'ит на исключении;
* ``UserRepository.is_tos_accepted`` — true/false по ``fetchval``;
* ``UserRepository.accept_tos`` — ``INSERT ... ON CONFLICT``;
* ``PaymentRepository.claim_payment_charge`` — True при INSERT,
  False при ``ON CONFLICT DO NOTHING``, False при пустом charge_id.
"""
from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from services.database import (
    PaymentRepository,
    UserRepository,
    db_transaction,
    init_postgres_pool,
)


# ── init_postgres_pool ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_postgres_pool_passes_security_timeouts(
    mocker: MockerFixture,
) -> None:
    """Контракт: command_timeout=5.0, connect_timeout=5.0, pool 10..50."""
    create_pool = mocker.patch(
        "services.database.connection.asyncpg.create_pool",
        new=mocker.AsyncMock(return_value=mocker.MagicMock(name="pool")),
    )

    pool = await init_postgres_pool("postgresql://localhost/x")

    create_pool.assert_awaited_once()
    kwargs = create_pool.call_args.kwargs
    assert kwargs["min_size"] == 10
    assert kwargs["max_size"] == 50
    assert kwargs["command_timeout"] == 5.0
    assert kwargs["timeout"] == 5.0
    assert pool is create_pool.return_value


@pytest.mark.asyncio
async def test_init_postgres_pool_rejects_empty_dsn() -> None:
    with pytest.raises(ValueError, match="DSN"):
        await init_postgres_pool("")


# ── db_transaction ───────────────────────────────────────────────────────


class _FakeTx:
    """Имитация ``conn.transaction()`` — context-manager,
    отслеживает commit/rollback по тому, было ли исключение."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.exc: BaseException | None = None

    async def __aenter__(self) -> "_FakeTx":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc is None:
            self.committed = True
        else:
            self.rolled_back = True
            self.exc = exc
        # Не подавляем исключение — asyncpg.Transaction поднимает наверх.


class _FakeAcquire:
    """Имитация ``pool.acquire()`` — context-manager отдаёт conn-стаб."""

    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _make_pool(mocker: MockerFixture) -> tuple[object, _FakeTx, object]:
    """Фабрика fake-пула. Возвращает (pool, tx, conn) для assert'ов."""
    tx = _FakeTx()
    conn = mocker.MagicMock(name="conn")
    conn.transaction.return_value = tx
    pool = mocker.MagicMock(name="pool")
    pool.acquire.return_value = _FakeAcquire(conn)
    return pool, tx, conn


@pytest.mark.asyncio
async def test_db_transaction_commits_on_success(
    mocker: MockerFixture,
) -> None:
    pool, tx, conn = _make_pool(mocker)

    async with db_transaction(pool) as yielded_conn:
        assert yielded_conn is conn

    assert tx.committed is True
    assert tx.rolled_back is False


@pytest.mark.asyncio
async def test_db_transaction_rolls_back_and_reraises_on_exception(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    pool, tx, _ = _make_pool(mocker)
    caplog.set_level("ERROR", logger="services.database.connection")

    with pytest.raises(RuntimeError, match="kaboom"):
        async with db_transaction(pool):
            raise RuntimeError("kaboom")

    assert tx.rolled_back is True
    assert tx.committed is False
    assert isinstance(tx.exc, RuntimeError)
    # exception-log с stacktrace для пост-mortem'а.
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert errors[0].exc_info is not None


# ── UserRepository ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_repo_is_tos_accepted_true(mocker: MockerFixture) -> None:
    conn = mocker.MagicMock()
    conn.fetchval = mocker.AsyncMock(return_value=True)

    repo = UserRepository(conn)
    result = await repo.is_tos_accepted(42)

    assert result is True
    conn.fetchval.assert_awaited_once()
    args, _ = conn.fetchval.call_args
    assert "FROM users" in args[0]
    assert args[1] == 42


@pytest.mark.asyncio
async def test_user_repo_is_tos_accepted_false_on_null(
    mocker: MockerFixture,
) -> None:
    """Если в БД нет строки или ``accepted_terms`` = NULL → False."""
    conn = mocker.MagicMock()
    conn.fetchval = mocker.AsyncMock(return_value=None)

    repo = UserRepository(conn)
    assert await repo.is_tos_accepted(42) is False


@pytest.mark.asyncio
async def test_user_repo_accept_tos_uses_upsert(
    mocker: MockerFixture,
) -> None:
    """accept_tos должен делать INSERT ... ON CONFLICT DO UPDATE,
    чтобы заодно гарантировать существование строки юзера."""
    conn = mocker.MagicMock()
    conn.execute = mocker.AsyncMock()

    repo = UserRepository(conn)
    await repo.accept_tos(42)

    conn.execute.assert_awaited_once()
    sql, *params = conn.execute.call_args.args
    assert "INSERT INTO users" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert "accepted_terms" in sql
    assert params == [42]


# ── PaymentRepository.claim_payment_charge ───────────────────────────────


@pytest.mark.asyncio
async def test_claim_charge_returns_true_on_first_insert(
    mocker: MockerFixture,
) -> None:
    """RETURNING вернул строку → claim создан."""
    conn = mocker.MagicMock()
    conn.fetchrow = mocker.AsyncMock(
        return_value={"telegram_payment_charge_id": "ch_abc"}
    )

    repo = PaymentRepository(conn)
    is_new = await repo.claim_payment_charge("ch_abc", 42, 3)

    assert is_new is True
    conn.fetchrow.assert_awaited_once()
    sql, *params = conn.fetchrow.call_args.args
    # Проверяем именно идемпотентную форму INSERT...ON CONFLICT.
    assert "INSERT INTO payment_charges" in sql
    assert "ON CONFLICT (telegram_payment_charge_id) DO NOTHING" in sql
    assert "RETURNING" in sql
    assert params == ["ch_abc", 42, 3]


@pytest.mark.asyncio
async def test_claim_charge_returns_false_on_conflict(
    mocker: MockerFixture,
) -> None:
    """RETURNING вернул NULL → DUPLICATE, начисление НЕ должно
    выполняться call-site'ом."""
    conn = mocker.MagicMock()
    conn.fetchrow = mocker.AsyncMock(return_value=None)

    repo = PaymentRepository(conn)
    is_new = await repo.claim_payment_charge("ch_dup", 42, 3)

    assert is_new is False


@pytest.mark.asyncio
async def test_claim_charge_returns_false_on_empty_id(
    mocker: MockerFixture,
) -> None:
    """Пустой charge_id — даже SQL не запускаем (быстрый exit).

    Пробелы НЕ режем — Telegram гарантирует корректный
    ``telegram_payment_charge_id`` (UUID-подобный). Пустая строка может
    прилететь только при ручном вызове из тестов.
    """
    conn = mocker.MagicMock()
    conn.fetchrow = mocker.AsyncMock()

    repo = PaymentRepository(conn)
    assert await repo.claim_payment_charge("", 42, 3) is False

    conn.fetchrow.assert_not_awaited()
