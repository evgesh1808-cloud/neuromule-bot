"""Валидация Telegram WebApp initData (HMAC-SHA256)."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from api.auth import (
    TelegramInitDataError,
    require_telegram_user,
    sign_init_data_for_tests,
    validate_telegram_init_data,
)

_TEST_BOT_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def test_validate_init_data_ok(monkeypatch) -> None:
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=42_001)
    user = validate_telegram_init_data(init_data)
    assert user.telegram_id == 42_001


def test_validate_init_data_rejects_tampered_hash(monkeypatch) -> None:
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=1)
    tampered = init_data.replace("hash=", "hash=deadbeef")
    with pytest.raises(TelegramInitDataError):
        validate_telegram_init_data(tampered)


def test_validate_init_data_rejects_expired(monkeypatch) -> None:
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)
    old_ts = int(time.time()) - 200_000
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=1, auth_date=old_ts)
    with pytest.raises(TelegramInitDataError, match="expired"):
        validate_telegram_init_data(init_data)


@pytest.mark.asyncio
async def test_require_telegram_user_dependency(monkeypatch) -> None:
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)
    init_data = sign_init_data_for_tests(_TEST_BOT_TOKEN, user_id=99)
    uid = await require_telegram_user(
        authorization=f"tma {init_data}",
        x_telegram_init_data=None,
    )
    assert uid == 99


@pytest.mark.asyncio
async def test_require_telegram_user_unauthorized(monkeypatch) -> None:
    monkeypatch.setattr("api.auth._bot_token", lambda: _TEST_BOT_TOKEN)
    with pytest.raises(HTTPException) as excinfo:
        await require_telegram_user(authorization=None, x_telegram_init_data=None)
    assert excinfo.value.status_code == 401
