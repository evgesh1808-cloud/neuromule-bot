"""Тесты Throttling Middleware (anti-fraud, 1 событие в 2 секунды на user)."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from platforms.telegram_throttling import (
    DEFAULT_ALERT_TEXT,
    ThrottlingMiddleware,
    reset_throttle,
)


class _StubUser:
    def __init__(self, uid: int) -> None:
        self.id = uid


class _StubCallback:
    """Минимальный аналог aiogram CallbackQuery для проверки middleware."""

    def __init__(self, uid: int, data: str = "make_video") -> None:
        self.from_user = _StubUser(uid)
        self.data = data
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class _StubMessage:
    def __init__(
        self,
        uid: int,
        text: str = "hello",
        *,
        document: Any | None = None,
    ) -> None:
        self.from_user = _StubUser(uid)
        self.text = text
        self.document = document


# Подделываем isinstance: aiogram-типы у нас не импортированы в этом наборе.
# Поэтому monkeypatch'им isinstance Hook через подмену типов в middleware.
import platforms.telegram_throttling as throttle_mod


@pytest.fixture(autouse=True)
def _patch_aiogram_types(monkeypatch: pytest.MonkeyPatch):
    """Подменяем aiogram CallbackQuery/Message на наши stubs."""
    monkeypatch.setattr(throttle_mod, "CallbackQuery", _StubCallback, raising=True)
    monkeypatch.setattr(throttle_mod, "Message", _StubMessage, raising=True)
    yield


@pytest.fixture(autouse=True)
def _reset_state():
    """Чистим внутренний словарь cooldown'ов перед каждым тестом."""
    throttle_mod._LAST_CALL_AT.clear()
    yield
    throttle_mod._LAST_CALL_AT.clear()


async def _noop_handler(event: Any, data: dict[str, Any]) -> str:
    return "executed"


@pytest.mark.asyncio
async def test_first_call_passes_through() -> None:
    mw = ThrottlingMiddleware(cooldown=2.0)
    cb = _StubCallback(uid=42)
    result = await mw(_noop_handler, cb, {})
    assert result == "executed"
    assert cb.answers == []


@pytest.mark.asyncio
async def test_second_call_within_cooldown_is_blocked() -> None:
    mw = ThrottlingMiddleware(cooldown=2.0)
    cb1 = _StubCallback(uid=42)
    cb2 = _StubCallback(uid=42)

    r1 = await mw(_noop_handler, cb1, {})
    r2 = await mw(_noop_handler, cb2, {})

    assert r1 == "executed"
    assert r2 is None
    # Юзер увидел плашку с дефолтным текстом, show_alert=False.
    assert cb2.answers == [(DEFAULT_ALERT_TEXT, False)]


@pytest.mark.asyncio
async def test_different_users_do_not_block_each_other() -> None:
    mw = ThrottlingMiddleware(cooldown=2.0)
    cb_a = _StubCallback(uid=1)
    cb_b = _StubCallback(uid=2)
    assert await mw(_noop_handler, cb_a, {}) == "executed"
    assert await mw(_noop_handler, cb_b, {}) == "executed"


@pytest.mark.asyncio
async def test_whitelist_callback_is_never_throttled() -> None:
    from content import messages as msg

    mw = ThrottlingMiddleware(cooldown=2.0)
    cb1 = _StubCallback(uid=42, data=msg.CB_GALLERY_CANCEL)
    cb2 = _StubCallback(uid=42, data=msg.CB_GALLERY_CANCEL)
    assert await mw(_noop_handler, cb1, {}) == "executed"
    # Сразу подряд — НЕ блокируется (whitelist).
    assert await mw(_noop_handler, cb2, {}) == "executed"
    assert cb1.answers == []
    assert cb2.answers == []


@pytest.mark.asyncio
async def test_admin_moderation_callback_is_never_throttled() -> None:
    from content import messages as msg

    mw = ThrottlingMiddleware(cooldown=2.0)
    data = f"{msg.CB_GALLERY_APPROVE_PREFIX}task_42"
    cb1 = _StubCallback(uid=1, data=data)
    cb2 = _StubCallback(uid=1, data=data)
    assert await mw(_noop_handler, cb1, {}) == "executed"
    assert await mw(_noop_handler, cb2, {}) == "executed"


@pytest.mark.asyncio
async def test_document_message_is_never_throttled() -> None:
    from types import SimpleNamespace

    mw = ThrottlingMiddleware(cooldown=2.0)
    doc = SimpleNamespace(file_name="report.xlsx")
    msg1 = _StubMessage(uid=42, document=doc)
    msg2 = _StubMessage(uid=42, document=doc)
    assert await mw(_noop_handler, msg1, {}) == "executed"
    assert await mw(_noop_handler, msg2, {}) == "executed"


@pytest.mark.asyncio
async def test_audit_platform_callback_is_never_throttled() -> None:
    from content import messages as msg

    mw = ThrottlingMiddleware(cooldown=2.0)
    data = f"{msg.CB_AUDIT_PLATFORM_PREFIX}wildberries"
    cb1 = _StubCallback(uid=42, data=data)
    cb2 = _StubCallback(uid=42, data=data)
    assert await mw(_noop_handler, cb1, {}) == "executed"
    assert await mw(_noop_handler, cb2, {}) == "executed"


@pytest.mark.asyncio
async def test_reset_throttle_clears_cooldown() -> None:
    mw = ThrottlingMiddleware(cooldown=2.0)
    cb = _StubCallback(uid=99)
    await mw(_noop_handler, cb, {})
    reset_throttle(99)
    cb2 = _StubCallback(uid=99)
    assert await mw(_noop_handler, cb2, {}) == "executed"
