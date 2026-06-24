"""Админское начисление ⚡ должно попадать в billing (energy_paid), не только в legacy energy."""

from __future__ import annotations

import pytest

from services.billing import billing
from services.billing.chat_pipeline import can_afford_role_minimum


@pytest.mark.asyncio
async def test_admin_energy_grant_visible_to_billing_after_spend(repo_module) -> None:
    uid = 88001
    await repo_module.ensure_user(uid)

    user_start = await billing.load_user(uid)
    while user_start.total_energy > 0:
        spent = await billing.resolve_and_charge_text_chat(uid, "standard")
        if spent.plan.blocked:
            break
        user_start = await billing.load_user(uid)

    user_empty = await billing.load_user(uid)
    assert user_empty.total_energy == 0
    assert user_empty.crystals == 0

    await repo_module.update_balance(uid, "energy", 100)
    user_after = await billing.load_user(uid)
    assert user_after.total_energy == 100
    assert can_afford_role_minimum(user_after, "table_generator") is True
