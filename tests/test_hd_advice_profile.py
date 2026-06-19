"""Профиль и hd_type для «Совета дня»."""

from __future__ import annotations

from services.hd_logic import (
    _HD_TYPE_UNDETERMINED,
    _resolve_hd_type_for_advice,
    build_daily_advice_prompt,
    daily_advice_user_profile_from_repo_user,
)


class _Row(dict):
    def keys(self):
        return super().keys()


def test_resolve_hd_type_paid_vs_newbie() -> None:
    assert _resolve_hd_type_for_advice("14.05.1990 Москва", "Генератор") == "Генератор"
    assert _resolve_hd_type_for_advice("", "Генератор") == _HD_TYPE_UNDETERMINED
    assert _resolve_hd_type_for_advice("14.05.1990", "") == _HD_TYPE_UNDETERMINED


def test_profile_newbie_gets_undetermined_type() -> None:
    user = _Row(
        advice_birth_data="14.05.1990 14:35 Москва",
        advice_user_role="",
        hd_birth_data="",
        hd_type="",
    )
    profile = daily_advice_user_profile_from_repo_user(user)
    assert profile is not None
    assert profile["hd_type"] == _HD_TYPE_UNDETERMINED
    assert profile["user_role"] == "по умолчанию"


def test_profile_paid_uses_db_hd_type() -> None:
    user = _Row(
        hd_birth_data="14.05.1990 14:35 Москва",
        hd_type="Проектор",
        advice_birth_data="",
        advice_user_role="мама",
    )
    profile = daily_advice_user_profile_from_repo_user(user)
    assert profile is not None
    assert profile["hd_type"] == "Проектор"
    assert profile["user_role"] == "мама"


def test_prompt_contains_undetermined_instruction() -> None:
    prompt = build_daily_advice_prompt(
        {
            "hd_type": _HD_TYPE_UNDETERMINED,
            "user_role": "по умолчанию",
            "birth_date": "14.05.1990",
            "birth_time": "14:35",
            "birth_place": "Москва",
        },
        current_cta_text="test cta",
    )
    assert _HD_TYPE_UNDETERMINED in prompt
    assert "НЕ ОПРЕДЕЛЕН" in prompt
    assert "ЗВЕЗДНЫЙ БАРОМЕТР" in prompt
