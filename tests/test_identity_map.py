"""Identity Map: разделение account_id и нативных ID платформ."""

from __future__ import annotations

import pytest

from services import repository as repo


@pytest.mark.asyncio
async def test_get_or_create_account_telegram_and_vk_distinct(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "identity.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    await repo.init_db()

    tg_account = await repo.get_or_create_account("telegram", 123)
    vk_account = await repo.get_or_create_account("vk", 123)
    assert tg_account != vk_account

    same_tg = await repo.get_or_create_account("telegram", 123)
    assert same_tg == tg_account

    legacy_tg = await repo.legacy_user_id_for_account(tg_account, platform="telegram")
    legacy_vk = await repo.legacy_user_id_for_account(vk_account, platform="vk")
    assert legacy_tg == 123
    assert legacy_vk == 123


@pytest.mark.asyncio
async def test_get_or_create_account_sets_users_account_id(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "backfill.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setattr(repo, "DB_PATH", str(db_path))
    await repo.init_db()
    await repo.ensure_user(555)

    account_id = await repo.get_or_create_account("telegram", 555)
    assert account_id > 0
    same = await repo.get_account_id_for_platform("telegram", 555)
    assert same == account_id
