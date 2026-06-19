"""Интеграционные тесты транзакционной логики «Совета дня» (services/repository.py)."""

from __future__ import annotations

from datetime import date

import aiosqlite
import pytest


async def _read_advice_state(repo_module, user_id: int) -> dict[str, object]:
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        async with db.execute(
            "SELECT last_free_date, advice_pending_at FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    return {
        "last_free_date": row[0],
        "advice_pending_at": row[1],
    }


async def _simulate_gemini_generation_failure(mocker) -> None:
    """Имитирует сбой Gemini так же, как это делает handler после try_begin."""
    mocker.patch(
        "services.hd_logic.generate_daily_forecast",
        side_effect=RuntimeError("Gemini API error"),
    )
    from services.hd_logic import generate_daily_forecast

    with pytest.raises(RuntimeError, match="Gemini API error"):
        await generate_daily_forecast(
            {
                "hd_type": "Генератор",
                "user_role": "предприниматель",
                "birth_date": "14.05.1990",
                "birth_time": "14:35",
                "birth_place": "Москва",
            },
            current_cta_text="test",
        )


async def test_successful_advice_flow(repo_module) -> None:
    """Идеальный сценарий: lock → commit → last_free_date = today."""
    uid = 88001
    today = date.today().isoformat()
    await repo_module.ensure_user(uid)

    state_before = await _read_advice_state(repo_module, uid)
    assert state_before["last_free_date"] is None
    assert state_before["advice_pending_at"] is None

    began = await repo_module.try_begin_daily_advice(uid)
    assert began is True

    locked = await _read_advice_state(repo_module, uid)
    assert locked["advice_pending_at"] is not None
    assert locked["last_free_date"] is None

    await repo_module.commit_daily_advice(uid)

    committed = await _read_advice_state(repo_module, uid)
    assert committed["advice_pending_at"] is None
    assert committed["last_free_date"] == today


async def test_anti_spam_lock(repo_module) -> None:
    """Пока advice_pending_at активен, повторный try_begin возвращает False."""
    uid = 88002
    await repo_module.ensure_user(uid)

    first = await repo_module.try_begin_daily_advice(uid)
    assert first is True

    second = await repo_module.try_begin_daily_advice(uid)
    assert second is False

    state = await _read_advice_state(repo_module, uid)
    assert state["advice_pending_at"] is not None
    assert state["last_free_date"] is None


async def test_rollback_on_gemini_error(repo_module, mocker) -> None:
    """При сбое Gemini rollback снимает lock, last_free_date не тратится."""
    uid = 88003
    await repo_module.ensure_user(uid)

    state_before = await _read_advice_state(repo_module, uid)
    assert state_before["last_free_date"] is None

    began = await repo_module.try_begin_daily_advice(uid)
    assert began is True

    locked = await _read_advice_state(repo_module, uid)
    assert locked["advice_pending_at"] is not None

    await _simulate_gemini_generation_failure(mocker)

    await repo_module.rollback_daily_advice(uid)

    state_after = await _read_advice_state(repo_module, uid)
    assert state_after["advice_pending_at"] is None
    assert state_after["last_free_date"] == state_before["last_free_date"]


async def test_reset_admin_daily_advice_test_state(repo_module) -> None:
    uid = 88004
    await repo_module.ensure_user(uid)
    today = date.today().isoformat()
    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET
                last_free_date = ?,
                advice_pending_at = 1.0,
                hd_type = 'Генератор',
                advice_birth_data = '14.05.1990 Москва'
            WHERE id = ?
            """,
            (today, uid),
        )
        await db.commit()

    await repo_module.reset_admin_daily_advice_test_state(uid)

    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        async with db.execute(
            "SELECT last_free_date, advice_pending_at, hd_type, advice_birth_data FROM users WHERE id = ?",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
    assert row[0] is None
    assert row[1] is None
    assert row[2] is None
    assert row[3] is None
