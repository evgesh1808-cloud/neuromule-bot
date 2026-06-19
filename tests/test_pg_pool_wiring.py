"""Тесты на проводку PG-пула в ``run_telegram()`` (PR-P · Phase 1a).

Проверяем lazy-активацию:

* пустой DSN → пул не создаётся, ``workflow_data["pg_pool"]``
  отсутствует, нет обращений к ``init_postgres_pool``;
* непустой DSN → ``init_postgres_pool`` вызывается ровно один раз,
  пул кладётся в ``workflow_data["pg_pool"]``;
* падение ``init_postgres_pool`` → бот НЕ падает, пул = ``None``,
  лог уровня ERROR с stacktrace'ом;
* ``asyncpg`` не установлен (``init_postgres_pool is None``) →
  ERROR-лог + graceful return ``None``.

Тестируется чистая функция ``_maybe_start_pg_pool(dp)`` — её достаточно,
чтобы зафиксировать контракт. Поднимать весь ``run_telegram()``
(с polling) в юнит-тестах нельзя — там реальный Telegram-токен и
``init_db``.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from platforms import telegram_bot as tb_mod


def _fake_dispatcher() -> SimpleNamespace:
    """Минимальный stub ``aiogram.Dispatcher`` — нам нужен только
    ``workflow_data: dict``, в который handler-инжектор aiogram'а
    кладёт зависимости."""
    return SimpleNamespace(workflow_data={})


# ── empty DSN ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_dsn_does_not_create_pool(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        tb_mod, "settings", SimpleNamespace(postgres_dsn="")
    )
    spy_init = mocker.patch.object(
        tb_mod,
        "init_postgres_pool",
        new=mocker.AsyncMock(),
    )

    dp = _fake_dispatcher()
    pool = await tb_mod._maybe_start_pg_pool(dp)

    assert pool is None
    assert "pg_pool" not in dp.workflow_data
    spy_init.assert_not_called()


@pytest.mark.asyncio
async def test_whitespace_dsn_treated_as_empty(
    mocker: MockerFixture,
) -> None:
    """DSN из ``.env`` может прилететь с пробелами — не должны
    активировать на пустой строке с whitespace."""
    mocker.patch.object(
        tb_mod, "settings", SimpleNamespace(postgres_dsn="   ")
    )
    spy_init = mocker.patch.object(
        tb_mod,
        "init_postgres_pool",
        new=mocker.AsyncMock(),
    )

    dp = _fake_dispatcher()
    pool = await tb_mod._maybe_start_pg_pool(dp)

    assert pool is None
    spy_init.assert_not_called()


# ── happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_empty_dsn_creates_and_attaches_pool(
    mocker: MockerFixture,
) -> None:
    fake_pool = mocker.MagicMock(name="pool")
    mocker.patch.object(
        tb_mod,
        "settings",
        SimpleNamespace(postgres_dsn="postgresql://x:y@host:5432/db"),
    )
    init_mock = mocker.patch.object(
        tb_mod,
        "init_postgres_pool",
        new=mocker.AsyncMock(return_value=fake_pool),
    )

    dp = _fake_dispatcher()
    pool = await tb_mod._maybe_start_pg_pool(dp)

    assert pool is fake_pool
    assert dp.workflow_data["pg_pool"] is fake_pool
    init_mock.assert_awaited_once_with("postgresql://x:y@host:5432/db")


# ── init failure ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_failure_returns_none_and_logs(
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PG недоступен → бот продолжает на SQLite, exception НЕ
    пробрасывается наружу."""
    caplog.set_level(logging.ERROR, logger=tb_mod.logger.name)

    mocker.patch.object(
        tb_mod, "settings", SimpleNamespace(postgres_dsn="postgresql://nope")
    )
    mocker.patch.object(
        tb_mod,
        "init_postgres_pool",
        new=mocker.AsyncMock(side_effect=RuntimeError("PG unreachable")),
    )

    dp = _fake_dispatcher()
    pool = await tb_mod._maybe_start_pg_pool(dp)

    assert pool is None
    assert "pg_pool" not in dp.workflow_data
    errors = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and r.name == tb_mod.logger.name
    ]
    assert errors, "ожидаем ERROR-лог с stacktrace'ом"
    assert errors[0].exc_info is not None


# ── asyncpg отсутствует в venv ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_asyncpg_missing_does_not_crash(
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Если venv разработчика без ``asyncpg``, проводка должна
    мягко отрезаться — никаких ``ImportError`` наружу."""
    caplog.set_level(logging.ERROR, logger=tb_mod.logger.name)

    mocker.patch.object(
        tb_mod, "settings", SimpleNamespace(postgres_dsn="postgresql://x/y")
    )
    mocker.patch.object(tb_mod, "init_postgres_pool", new=None)

    dp = _fake_dispatcher()
    pool = await tb_mod._maybe_start_pg_pool(dp)

    assert pool is None
    assert "pg_pool" not in dp.workflow_data
    msgs = [r.getMessage() for r in caplog.records]
    assert any("asyncpg" in m for m in msgs)
