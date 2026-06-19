"""Тесты подключения DUO к биллингу:

- `resolve_duo_owner` редиректит списания на кошелёк ULTRA-владельца.
- `load_user_billing(member)` возвращает балансы owner.
- `atomic_spend(member, ...)` списывает с owner-balance, photo_daily — у owner,
  billing_charges.user_id = owner (т.е. refund вернёт на owner-кошелёк).
"""

from __future__ import annotations

import pytest

from services.billing.store import (
    atomic_spend,
    init_billing_schema,
    load_user_billing,
    refund_charge,
)
from datetime import date, timedelta

from services.billing import store
from services.family_sharing import link_duo_partner


@pytest.mark.asyncio
async def test_member_spends_from_owner_wallet(repo_module) -> None:
    owner = 60001
    member = 60002
    await repo_module.ensure_user(owner)
    await repo_module.ensure_user(member)
    await repo_module.set_user_tariff(owner, "ULTRA")
    await init_billing_schema()
    await store.grant_balance_package(
        owner,
        kind="ULTRA_1MONTH",
        energy_amount=1,
        crystals_amount=0,
        expires_at=(date.today() + timedelta(days=30)).isoformat(),
    )
    import aiosqlite

    from services import repository

    # У owner: 100 ⚡ paid, 50 💎 sub
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET energy_paid = 100, energy_free = 0, energy = 100, "
            "balance_energy = 100, sub_crystals = 50, buy_crystals = 0, "
            "crystals = 50 WHERE id = ?",
            (owner,),
        )
        await db.commit()
    ok, _ = await link_duo_partner(owner, member)
    assert ok is True

    # Партнёр видит баланс владельца DUO
    state_member = await load_user_billing(member)
    assert state_member.crystals == 50

    # Member спендит 10 💎 — деньги уходят с owner-кошелька
    charge = await atomic_spend(
        member,
        "test_feature",
        energy_need=0,
        crystal_need=10,
        crystals_only=True,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    assert charge is not None
    assert charge.crystals == 10

    # У owner стало 40 💎, у member свой crystals остался прежним
    owner_state = await load_user_billing(owner)
    assert owner_state.crystals == 40

    async with aiosqlite.connect(repository.DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM billing_charges WHERE charge_id = ?",
            (charge.charge_id,),
        ) as cur:
            row = await cur.fetchone()
    # billing_charges.user_id = owner_id, чтобы refund вернул на правильный кошелёк
    assert int(row[0]) == owner

    # Refund возвращает на owner-balance, member видит обновлённый баланс
    assert await refund_charge(charge.charge_id) is True
    refunded_state = await load_user_billing(owner)
    assert refunded_state.crystals == 50
    member_state_after = await load_user_billing(member)
    assert member_state_after.crystals == 50


@pytest.mark.asyncio
async def test_solo_user_unaffected_by_duo_routing(repo_module) -> None:
    """Контрольный тест: для не-member ничего не изменилось."""
    solo = 60050
    await repo_module.ensure_user(solo)
    await repo_module.set_user_tariff(solo, "SMART")
    await init_billing_schema()
    import aiosqlite

    from services import repository

    async with aiosqlite.connect(repository.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET energy_paid = 100, energy_free = 0, energy = 100, "
            "balance_energy = 100 WHERE id = ?",
            (solo,),
        )
        await db.commit()

    charge = await atomic_spend(
        solo,
        "solo_feature",
        energy_need=5,
        crystal_need=0,
        crystals_only=False,
        reserve_photo_slot=False,
        photo_daily_limit=0,
    )
    assert charge is not None
    assert charge.energy_paid + charge.energy_free == 5
    state = await load_user_billing(solo)
    assert state.energy_paid == 95
