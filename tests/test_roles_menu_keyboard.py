"""Главное меню ролей NeuroMule и подменю лайфстайл."""

from __future__ import annotations

import pytest

from content import messages as msg
from platforms.telegram_keyboards import (
    LIFESTYLE_SUBROLES,
    create_lifestyle_subroles_keyboard,
    create_roles_menu_keyboard,
)


@pytest.mark.asyncio
async def test_create_roles_menu_keyboard_layout(repo_module) -> None:
    uid = 88001
    await repo_module.ensure_user(uid)
    kb = await create_roles_menu_keyboard(uid, "standard")
    assert len(kb.inline_keyboard) == 5
    assert kb.inline_keyboard[0][0].callback_data == f"{msg.CB_SET_ROLE_PREFIX}standard"
    assert kb.inline_keyboard[0][1].callback_data == f"{msg.CB_SET_ROLE_PREFIX}summary"
    assert kb.inline_keyboard[1][0].callback_data == msg.CB_SHOW_TABLE_SUBCATEGORIES
    assert kb.inline_keyboard[2][0].callback_data == f"{msg.CB_SET_ROLE_PREFIX}podcast_doc"
    assert kb.inline_keyboard[3][0].callback_data == msg.CB_SHOW_LIFESTYLE_SUBCATEGORIES
    assert kb.inline_keyboard[4][0].callback_data == msg.CB_NEW_DIALOG
    assert kb.inline_keyboard[4][1].callback_data == msg.CB_BACK_TO_TOOLS


def test_lifestyle_subroles_keyboard() -> None:
    kb = create_lifestyle_subroles_keyboard(active_role_id="blogger_content")
    assert len(kb.inline_keyboard) == 3
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    for _label, role_id in LIFESTYLE_SUBROLES:
        assert f"{msg.CB_SET_ROLE_PREFIX}{role_id}" in callbacks
    assert msg.CB_BACK_TO_ROLES_MENU in callbacks
    blogger_btn = kb.inline_keyboard[0][0]
    assert "✅" in blogger_btn.text
