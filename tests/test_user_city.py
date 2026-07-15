"""Локация пользователя для локальных хэштегов блогера."""

from __future__ import annotations

import pytest

from services.repository import (
    DEFAULT_USER_CITY,
    get_user_city,
    is_default_user_city,
    normalize_user_city,
    set_user_city,
)


def test_normalize_and_default_city() -> None:
    assert DEFAULT_USER_CITY == "Чебоксары"
    assert normalize_user_city("") == "Чебоксары"
    assert normalize_user_city("  Люберцы ") == "Люберцы"
    assert is_default_user_city(None) is True
    assert is_default_user_city("Чебоксары") is True
    assert is_default_user_city("Люберцы") is False


@pytest.mark.asyncio
async def test_get_set_user_city_roundtrip(repo_module) -> None:
    uid = 880_017
    assert await get_user_city(uid) == DEFAULT_USER_CITY
    saved = await set_user_city(uid, "Жулебино")
    assert saved == "Жулебино"
    assert await get_user_city(uid) == "Жулебино"
    assert is_default_user_city(await get_user_city(uid)) is False
