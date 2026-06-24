"""Нейротекст: выбор роли и ограничения тарифа."""

from __future__ import annotations

import pytest

from services.use_cases.neurotext_turn import (
    NeurotextRoleOutcome,
    build_neurotext_intro,
    validate_text_role_pick,
)


@pytest.mark.asyncio
async def test_free_user_cannot_pick_expert_role(repo_module) -> None:
    uid = 77001
    await repo_module.ensure_user(uid)
    r = await validate_text_role_pick(uid, "psychologist_coach")
    assert r.outcome is NeurotextRoleOutcome.PREMIUM_LOCKED


@pytest.mark.asyncio
async def test_free_user_can_pick_standard(repo_module) -> None:
    uid = 77002
    await repo_module.ensure_user(uid)
    r = await validate_text_role_pick(uid, "standard")
    assert r.outcome is NeurotextRoleOutcome.OK
    assert r.role_id == "standard"


@pytest.mark.asyncio
async def test_paid_user_can_pick_expert(repo_module) -> None:
    uid = 77003
    await repo_module.ensure_user(uid)
    await repo_module.set_user_tariff(uid, "MINI")
    r = await validate_text_role_pick(uid, "blogger_content")
    assert r.outcome is NeurotextRoleOutcome.OK


@pytest.mark.asyncio
async def test_intro_free_has_base_header(repo_module) -> None:
    uid = 77004
    await repo_module.ensure_user(uid)
    text = await build_neurotext_intro(uid, "standard")
    assert "Базовая версия" in text
    assert "NeuroMule" in text
    assert "Стандарт" in text
    assert "💎" in text


@pytest.mark.asyncio
async def test_intro_paid_has_premium_header(repo_module) -> None:
    uid = 77005
    await repo_module.ensure_user(uid)
    await repo_module.set_user_tariff(uid, "SMART")
    text = await build_neurotext_intro(uid, "summary")
    assert "Премиум" in text
    assert "SMART" in text
    assert "Саммари" in text


@pytest.mark.asyncio
async def test_intro_mini_header_and_podcast_locked(repo_module) -> None:
    uid = 77007
    await repo_module.ensure_user(uid)
    await repo_module.set_user_tariff(uid, "MINI")
    await repo_module.update_balance(uid, "energy", 50)

    intro = await build_neurotext_intro(uid, "blogger_content")
    assert "Премиум" in intro
    assert "MINI" in intro
    assert "Блогер" in intro

    from services.use_cases.neurotext_turn import NeurotextRoleOutcome, get_role_availability_map

    avail = await get_role_availability_map(uid)
    assert avail["blogger_content"].locked is False
    assert avail["podcast_doc"].locked is True
    assert avail["podcast_doc"].locked_reason == "smart"

    pick = await validate_text_role_pick(uid, "podcast_doc")
    assert pick.outcome is NeurotextRoleOutcome.SMART_REQUIRED


@pytest.mark.asyncio
async def test_intro_insufficient_alert(repo_module) -> None:
    uid = 77006
    await repo_module.ensure_user(uid)
    text = await build_neurotext_intro(uid, "table_generator")
    assert "Недостаточно" in text
