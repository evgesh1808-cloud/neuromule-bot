"""Тесты архетипов HD-профилей."""

from __future__ import annotations

import pytest

from services.hd_profile_archetypes import (
    format_profile_archetype_for_user,
    profile_archetype_label,
    text_contains_raw_profile_code,
)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("1/3", "Исследователь-Практик"),
        ("3/5", "Экспериментатор-Спасатель"),
        ("6/2", "Наставник-Отшельник"),
    ],
)
def test_profile_archetype_mapping(profile: str, expected: str) -> None:
    assert profile_archetype_label(profile) == expected
    assert expected in format_profile_archetype_for_user(profile)


def test_profile_archetype_includes_hint() -> None:
    text = format_profile_archetype_for_user("3/5")
    assert "Экспериментатор-Спасатель" in text
    assert "кризис-менеджер" in text


def test_text_contains_raw_profile_code() -> None:
    assert text_contains_raw_profile_code("Профиль 3/5 в тексте")
    assert not text_contains_raw_profile_code("Экспериментатор-Спасатель")
