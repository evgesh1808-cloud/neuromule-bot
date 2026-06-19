"""Общие фикстуры pytest.

Содержит:

* ``repo_module`` — изолированная SQLite для legacy-флоу.
* ``pg_test_dsn`` / ``pg_pool`` / ``pg_conn`` — фикстуры для
  ``@pytest.mark.integration`` тестов против реального PostgreSQL.
  Активируются только при заданной env-переменной
  ``POSTGRES_TEST_DSN`` (формат: postgresql://user:pass@host:5432/db).
* Hook ``pytest_collection_modifyitems`` — авто-skip всех integration
  тестов, если DSN не задан. Это значит, что обычный ``pytest -q`` в
  CI без PG продолжает зеленеть.

Запуск integration-тестов:

    # 1. Поднять тестовый PG:
    docker compose -f docker-compose.test.yml up -d
    # 2. Накатить схему:
    POSTGRES_DSN=postgresql://test:test@127.0.0.1:55432/neuromule_test \
        python tools/init_postgres.py
    # 3. Прогнать integration-тесты:
    POSTGRES_TEST_DSN=postgresql://test:test@127.0.0.1:55432/neuromule_test \
        python -m pytest tests/integration -v -m integration
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import aiosqlite
import pytest
import pytest_asyncio


# ── Изоляция глобальных настроек ──────────────────────────────────────────

# Фиксированный тестовый кортеж администраторов. Используется автофикстурой
# ниже, а также может импортироваться тестами для построения позитивных и
# негативных кейсов («админ / не админ») без обращения к ``.env``.
TEST_ADMIN_IDS: tuple[int, ...] = (999111, 999222)


@pytest.fixture(autouse=True)
def _isolate_settings_for_tests() -> Iterator[None]:
    """Безопасно подменяет глобальные ``config.settings`` для каждого теста.

    Подменяемые поля (детерминированные значения вне зависимости от ``.env``):

    * ``settings.admin_ids = list(TEST_ADMIN_IDS)`` — фиксированный кортеж
      ``(999111, 999222)`` для построения «админ / не админ» кейсов.
    * ``settings.is_webapp_enabled = False`` — текстовый режим по умолчанию;
      кейсы с WebApp-режимом выставляют флаг локально через
      ``object.__setattr__`` внутри теста.
    * ``settings.webapp_shop_url = None`` — отсутствие фронта Mini App;
      кейсы с WebApp-режимом задают URL локально.

    Реализация:
        ``config.Settings`` — это ``frozen`` ``pydantic.BaseSettings`` (Pydantic
        v2). ``monkeypatch.setattr`` падает с ``Instance is frozen``. Поэтому
        подмена/восстановление идут через ``object.__setattr__`` — он обходит
        дескриптор ``__setattr__`` модели и не запускает frozen-валидацию.

    Teardown:
        В блоке ``finally`` все оригинальные значения возвращаются на место.
        Никакой утечки тестовых данных в другие тесты или прод-код.
    """

    from config import settings

    sentinel = object()
    fields_overrides: tuple[tuple[str, object], ...] = (
        ("admin_ids", list(TEST_ADMIN_IDS)),
        ("is_webapp_enabled", False),
        ("webapp_shop_url", None),
    )
    originals: dict[str, object] = {}
    for name, override in fields_overrides:
        originals[name] = getattr(settings, name, sentinel)
        object.__setattr__(settings, name, override)
    try:
        yield
    finally:
        for name, original in originals.items():
            if original is sentinel:
                continue
            object.__setattr__(settings, name, original)


# ── Legacy SQLite-фикстура (используется большинством тестов) ────────────


@pytest_asyncio.fixture
async def repo_module(monkeypatch, tmp_path) -> AsyncIterator:
    import services.repository as repository

    db_file = tmp_path / "pytest_neuromule.db"
    monkeypatch.setattr(repository, "DB_PATH", str(db_file))
    await repository.init_db("")
    try:
        yield repository
    finally:
        async with aiosqlite.connect(repository.DB_PATH) as db:
            await db.execute("DELETE FROM rate_limit_hits")
            await db.commit()


# ── PostgreSQL integration-фикстуры (PR-P · Phase 1a) ────────────────────


def pytest_collection_modifyitems(config, items) -> None:
    """Без ``POSTGRES_TEST_DSN`` все ``@pytest.mark.integration`` тесты
    помечаются ``skip`` с понятной причиной. Это гарантирует, что
    обычный ``python -m pytest -q`` не падает на машинах без PG."""

    if os.environ.get("POSTGRES_TEST_DSN", "").strip():
        return
    skip_marker = pytest.mark.skip(
        reason="POSTGRES_TEST_DSN не задан — integration-тесты пропущены "
        "(см. docs/PHASE1A_TEST_PLAN.md, поднимите docker-compose.test.yml)."
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def pg_test_dsn() -> str:
    """DSN тестового PG. Тест без integration-маркера до этой
    фикстуры не дойдёт (skip раньше)."""
    dsn = os.environ.get("POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("POSTGRES_TEST_DSN не задан")
    return dsn


@pytest_asyncio.fixture(scope="session")
async def pg_pool(pg_test_dsn: str):
    """Один пул на всю сессию integration-тестов — экономим время
    на хэндшейках TLS/PG-startup.

    Используем production-фабрику ``init_postgres_pool``, чтобы
    тесты гоняли тот же контракт пула, что и продакшен."""

    from services.database import init_postgres_pool

    pool = await init_postgres_pool(pg_test_dsn)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def clean_pg(pg_pool) -> AsyncIterator[None]:
    """Чистая БД перед каждым тестом. ``TRUNCATE ... CASCADE`` —
    самый быстрый способ обнулить тестовый dataset (быстрее, чем
    DELETE, и сбрасывает sequences под `RESTART IDENTITY`)."""

    async with pg_pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE TABLE payment_events, payment_charges, users "
            "RESTART IDENTITY CASCADE"
        )
    yield
    # Не очищаем после: следующий тест начнёт с чистого листа сам.


@pytest_asyncio.fixture
async def pg_conn(pg_pool, clean_pg) -> AsyncIterator:
    """Свежий connection с дефолтной транзакцией внутри ―
    удобно для тестов, которые работают с одним юзером и не хотят
    оборачиваться в ``db_transaction`` вручную.

    На выходе транзакция роллбэкается (а не коммитится), чтобы
    тест-эффекты не утекали в следующий тест."""

    async with pg_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            yield conn
        finally:
            await tx.rollback()
