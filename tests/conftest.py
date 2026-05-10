"""Общие фикстуры: изолированная SQLite, не трогаем neuromule_base.db проекта."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiosqlite
import pytest_asyncio


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
