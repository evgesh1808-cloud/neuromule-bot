"""Animate motion survey + GPT director."""

from __future__ import annotations

import pytest

from services.animate_motion import (
    build_motion_draft_from_choices,
    resolve_animate_participants,
)
from services.photo_edit_session import reset_photo_edit_sessions_for_tests, save_photo_edit_session
from services.openrouter_videos import (
    ANIMATE_DEFAULT_PROMPT,
    ANIMATE_FIXED_DURATION_SEC,
    expand_motion_prompt_with_gpt,
    resolve_animate_duration_for_model,
)


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    reset_photo_edit_sessions_for_tests()
    yield
    reset_photo_edit_sessions_for_tests()


def test_resolve_animate_duration_is_fixed_four_seconds() -> None:
    assert resolve_animate_duration_for_model("google/veo-3.1-lite") == 4
    assert resolve_animate_duration_for_model("bytedance/seedance-2.0-mini") == 4
    assert ANIMATE_FIXED_DURATION_SEC == 4


def test_resolve_animate_participants_from_final_roles() -> None:
    save_photo_edit_session(
        1,
        image_model_id="nano_banana_pro",
        image_model_label="Nano",
        telegram_file_id="AgAC_x",
        final_roles=("папа", "мама", "дочка", "сын"),
    )
    from services.photo_edit_session import get_photo_edit_session

    sess = get_photo_edit_session(1)
    assert sess is not None
    parts = resolve_animate_participants(sess)
    assert len(parts.people) == 4
    assert parts.pet is None
    assert parts.people[0].display_label == "Папа"
    assert parts.people[2].display_label == "Дочка"


def test_build_motion_draft_from_choices() -> None:
    from services.animate_motion import AnimateParticipants, AnimatePerson

    participants = AnimateParticipants(
        people=(
            AnimatePerson(ref_index=0, role_key="папа", display_label="Папа"),
            AnimatePerson(ref_index=1, role_key="мама", display_label="Мама"),
        ),
    )
    draft = build_motion_draft_from_choices(
        participants,
        {
            "p:0": "softly smiles into the camera",
            "p:1": "slowly turns head to look at the person below",
        },
    )
    assert "Person 1 (Папа" in draft
    assert "Person 2 (Мама" in draft
    assert "softly smiles" in draft


async def test_expand_motion_prompt_with_gpt_fallback_without_api_key() -> None:
    from config import Settings

    settings = Settings(tg_token="x", openrouter_key="")
    out = await expand_motion_prompt_with_gpt(settings, "Person 1: smiles")
    assert "NO yawning" in out


async def test_expand_motion_prompt_with_gpt_uses_director(monkeypatch) -> None:
    from config import Settings

    async def _fake_ask(*_a, **_k):
        return {"content": "Person 1 smiles gently. mouths closed."}

    monkeypatch.setattr("services.ai_text.ask_ai_messages", _fake_ask)
    settings = Settings(tg_token="x", openrouter_key="sk-test")
    out = await expand_motion_prompt_with_gpt(settings, "Person 1 (Папа): smiles")
    assert "Person 1 smiles gently" in out
    assert "NO yawning" in out


def test_default_animate_prompt_blocks_yawning_and_smiling() -> None:
    assert "NO yawning" in ANIMATE_DEFAULT_PROMPT
    assert "NO smiling" in ANIMATE_DEFAULT_PROMPT


def test_infer_ref_count_from_peek_wall_prompt() -> None:
    from services.animate_motion import infer_ref_count_from_prompt, resolve_final_roles_from_context

    prompt = (
        "семья подглядывает из-за стены: папа input_references[0], "
        "мама input_references[1], дочка input_references[2], сын input_references[3]"
    )
    assert infer_ref_count_from_prompt(prompt) == 4
    roles = resolve_final_roles_from_context(user_prompt=prompt)
    assert len(roles) == 4
    assert roles[0] == "папа"


def test_resolve_extended_family_roles_from_prompt() -> None:
    from services.animate_motion import resolve_animate_participants, resolve_final_roles_from_context

    prompt = (
        "семейное фото: дедушка input_references[0], бабушка input_references[1], "
        "тётя input_references[2], племянник input_references[3]"
    )
    roles = resolve_final_roles_from_context(user_prompt=prompt)
    assert len(roles) == 4
    assert roles[0] == "дедушка"
    assert roles[2] == "тётя"

    parts = resolve_animate_participants(None)
    assert len(parts.people) == 1  # без сессии — fallback

    from services.photo_edit_session import save_photo_edit_session

    save_photo_edit_session(
        99,
        image_model_id="nano",
        image_model_label="Nano",
        telegram_file_id="AgAC_x",
        user_prompt=prompt,
        final_roles=tuple(roles),
    )
    from services.photo_edit_session import get_photo_edit_session

    sess = get_photo_edit_session(99)
    parts = resolve_animate_participants(sess)
    assert len(parts.people) == 4
    assert parts.people[0].display_label == "Дедушка"
    assert parts.people[3].display_label == "Племянник"


def test_format_participant_list_ru() -> None:
    from services.animate_motion import AnimateParticipants, AnimatePerson, format_participant_list_ru

    parts = AnimateParticipants(
        people=(
            AnimatePerson(0, "дедушка", "Дедушка"),
            AnimatePerson(1, "бабушка", "Бабушка"),
            AnimatePerson(2, "внук", "Внук"),
        ),
    )
    assert format_participant_list_ru(parts) == "Дедушка, Бабушка и Внук"


def test_resolve_roles_from_stored_final_roles_without_group_refs() -> None:
    from services.animate_motion import resolve_final_roles_from_context

    roles = resolve_final_roles_from_context(
        final_roles=("папа", "мама", "дочка", "сын"),
    )
    assert roles == ["папа", "мама", "дочка", "сын"]


@pytest.mark.asyncio
async def test_admin_animate_bypass_skips_tariff_and_balance(repo_module, monkeypatch) -> None:
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    from unittest.mock import MagicMock

    from config import settings
    from platforms.handlers.animate_motion_fsm import start_animate_motion_survey
    from services.god_mode import GOD_MODE_CHARGE_ID, admin_animate_bypass
    from tests.conftest import TEST_ADMIN_IDS

    admin_id = TEST_ADMIN_IDS[0]
    object.__setattr__(settings, "god_mode_enabled", False)
    assert admin_animate_bypass(admin_id) is True

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=MagicMock(chat_id=admin_id, user_id=admin_id, bot_id=1))

    result = await start_animate_motion_survey(
        user_id=admin_id,
        chat_id=admin_id,
        file_id="AgAC_test",
        state=state,
        session=None,
    )
    assert result is None

    from services.billing.hd_pipeline import spend_animate

    spend = await spend_animate(admin_id)
    assert spend.ok is True
    assert spend.charge is not None
    assert spend.charge.charge_id == GOD_MODE_CHARGE_ID
