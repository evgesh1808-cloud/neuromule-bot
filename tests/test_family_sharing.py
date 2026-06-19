"""Опция DUO: миграция, связывание, роутер кошелька."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from services.billing import store
from services.family_sharing import (
    MAX_DUO_MEMBERS,
    is_duo_owner,
    is_duo_owner_eligible,
    link_duo_partner,
    list_duo_partners,
    resolve_duo_owner,
    unlink_duo_partner,
)


async def _grant_monthly_ultra(repo_module, uid: int) -> None:
    await repo_module.set_user_tariff(uid, "ULTRA")
    await store.init_billing_schema()
    expires = (date.today() + timedelta(days=30)).isoformat()
    await store.grant_balance_package(
        uid,
        kind="ULTRA_1MONTH",
        energy_amount=100,
        crystals_amount=10,
        expires_at=expires,
    )


@pytest.mark.asyncio
async def test_link_requires_monthly_ultra_owner(repo_module) -> None:
    owner = 55001
    member = 55002
    await repo_module.ensure_user(owner)
    await repo_module.ensure_user(member)
    await repo_module.set_user_tariff(owner, "MINI")
    ok, err = await link_duo_partner(owner, member)
    assert ok is False
    assert err == "not_duo_eligible"


@pytest.mark.asyncio
async def test_ultra_without_monthly_pack_cannot_link(repo_module) -> None:
    """ULTRA 3 дня / 1 неделя — без DUO."""
    owner = 55003
    member = 55004
    await repo_module.ensure_user(owner)
    await repo_module.ensure_user(member)
    await repo_module.set_user_tariff(owner, "ULTRA")
    await store.grant_balance_package(
        owner,
        kind="ULTRA_3DAYS",
        energy_amount=100,
        crystals_amount=5,
        expires_at=(date.today() + timedelta(days=3)).isoformat(),
    )
    ok, err = await link_duo_partner(owner, member)
    assert not ok and err == "not_duo_eligible"


@pytest.mark.asyncio
async def test_link_and_resolve_owner(repo_module) -> None:
    owner = 55010
    partner = 55011
    for uid in (owner, partner):
        await repo_module.ensure_user(uid)
    await _grant_monthly_ultra(repo_module, owner)

    ok, _ = await link_duo_partner(owner, partner)
    assert ok

    partners = await list_duo_partners(owner)
    assert partners == [partner]
    assert await is_duo_owner(owner) is True

    assert await resolve_duo_owner(partner) == owner
    assert await resolve_duo_owner(owner) == owner
    assert await resolve_duo_owner(99999) == 99999


@pytest.mark.asyncio
async def test_link_rejects_self_and_double_link(repo_module) -> None:
    owner = 55020
    partner = 55021
    other_owner = 55022
    for uid in (owner, partner, other_owner):
        await repo_module.ensure_user(uid)
    await _grant_monthly_ultra(repo_module, owner)
    await _grant_monthly_ultra(repo_module, other_owner)

    ok_self, err_self = await link_duo_partner(owner, owner)
    assert not ok_self and err_self == "self"

    ok, _ = await link_duo_partner(owner, partner)
    assert ok is True

    ok_dup, err_dup = await link_duo_partner(other_owner, partner)
    assert not ok_dup and err_dup == "already_linked"


@pytest.mark.asyncio
async def test_link_limit_reached(repo_module) -> None:
    owner = 55030
    p1 = 55100
    p2 = 55101
    await repo_module.ensure_user(owner)
    await repo_module.ensure_user(p1)
    await repo_module.ensure_user(p2)
    await _grant_monthly_ultra(repo_module, owner)

    ok1, _ = await link_duo_partner(owner, p1)
    assert ok1 is True

    ok2, err = await link_duo_partner(owner, p2)
    assert not ok2 and err == "limit_reached"
    assert MAX_DUO_MEMBERS == 1


@pytest.mark.asyncio
async def test_resolve_falls_back_when_owner_loses_duo(repo_module) -> None:
    owner = 55040
    partner = 55041
    for uid in (owner, partner):
        await repo_module.ensure_user(uid)
    await _grant_monthly_ultra(repo_module, owner)
    await link_duo_partner(owner, partner)
    assert await resolve_duo_owner(partner) == owner

    await repo_module.set_user_tariff(owner, "MINI")
    assert await resolve_duo_owner(partner) == partner
    assert await list_duo_partners(owner) == []


@pytest.mark.asyncio
async def test_unlink_duo_partner(repo_module) -> None:
    owner = 55050
    partner = 55051
    for uid in (owner, partner):
        await repo_module.ensure_user(uid)
    await _grant_monthly_ultra(repo_module, owner)
    await link_duo_partner(owner, partner)
    assert await unlink_duo_partner(owner, partner) is True
    assert await resolve_duo_owner(partner) == partner
    assert await unlink_duo_partner(owner, partner) is False


@pytest.mark.asyncio
async def test_is_duo_owner_eligible(repo_module) -> None:
    uid = 55060
    await repo_module.ensure_user(uid)
    assert await is_duo_owner_eligible(uid) is False
    await _grant_monthly_ultra(repo_module, uid)
    assert await is_duo_owner_eligible(uid) is True
