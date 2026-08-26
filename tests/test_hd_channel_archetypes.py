"""Тесты архетипов HD-каналов."""

from __future__ import annotations

import pytest

from services.hd_channel_archetypes import (
    channel_superpower_label,
    format_channel_superpower_for_user,
    normalize_channel_code,
    text_contains_raw_channel_code,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20-34", "20-34"),
        ("34-20", "20-34"),
        ("20 – 34", "20-34"),
    ],
)
def test_normalize_channel_code(raw: str, expected: str) -> None:
    assert normalize_channel_code(raw) == expected


def test_channel_superpower_20_34() -> None:
    assert channel_superpower_label("20-34") == "Суперсила влияния в моменте"
    assert format_channel_superpower_for_user("34-20") == "Суперсила влияния в моменте"


def test_text_contains_raw_channel_code() -> None:
    assert text_contains_raw_channel_code("Канал 20-34 активен")
    assert not text_contains_raw_channel_code("Суперсила влияния в моменте")
