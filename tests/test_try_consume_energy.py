"""Списание энергии: достаточно / недостаточно / нулевая сумма."""

from __future__ import annotations


async def test_try_consume_energy_success_and_balance(repo_module):
    uid = 71001
    await repo_module.ensure_user(uid)
    await repo_module.update_balance(uid, "energy", 100)

    ok = await repo_module.try_consume_energy(uid, 30)
    assert ok is True
    row = await repo_module.get_user_row(uid)
    # ensure_user: 20, +100, −30
    assert row.energy == 90


async def test_try_consume_energy_insufficient(repo_module):
    uid = 71002
    await repo_module.ensure_user(uid)
    row = await repo_module.get_user_row(uid)
    start = row.energy

    ok = await repo_module.try_consume_energy(uid, start + 1)
    assert ok is False
    row_after = await repo_module.get_user_row(uid)
    assert row_after.energy == start


async def test_try_consume_energy_zero_amount_noop(repo_module):
    uid = 71003
    await repo_module.ensure_user(uid)
    row_before = await repo_module.get_user_row(uid)

    ok = await repo_module.try_consume_energy(uid, 0)
    assert ok is True
    row_after = await repo_module.get_user_row(uid)
    assert row_after.energy == row_before.energy
