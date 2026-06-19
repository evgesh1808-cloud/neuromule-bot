"""PR-F: добавочные интеграционные тесты ThrottlingMiddleware.

Покрытие, недостающее в ``test_throttling_middleware.py``:

* системные апдейты (PreCheckoutQuery / InlineQuery) НЕ троттлятся;
* Message внутри cooldown'а блокируется БЕЗ callback.answer (нет UX-шума);
* по истечении cooldown'а событие пропускается.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pytest_mock import MockerFixture

from platforms import telegram_throttling as throttle_mod
from platforms.telegram_throttling import ThrottlingMiddleware


class _StubUser:
    def __init__(self, uid: int) -> None:
        self.id = uid


class _StubMessage:
    def __init__(self, uid: int) -> None:
        self.from_user = _StubUser(uid)
        self.text = "hi"


class _StubCallback:
    def __init__(self, uid: int, data: str = "make_video") -> None:
        self.from_user = _StubUser(uid)
        self.data = data
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


@pytest.fixture(autouse=True)
def _patch_aiogram_types(mocker: MockerFixture):
    mocker.patch.object(throttle_mod, "CallbackQuery", _StubCallback)
    mocker.patch.object(throttle_mod, "Message", _StubMessage)
    yield


@pytest.fixture(autouse=True)
def _reset_state():
    throttle_mod._LAST_CALL_AT.clear()
    yield
    throttle_mod._LAST_CALL_AT.clear()


async def _ok_handler(event: Any, data: dict[str, Any]) -> str:
    return "executed"


# ─────────────────────────────────────────────────────────────────────────
# Системные апдейты не троттлятся
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_message_non_callback_event_passes_through() -> None:
    """Иной тип апдейта (например, PreCheckoutQuery) — без cooldown."""
    mw = ThrottlingMiddleware(cooldown=2.0)
    # Объект, который НЕ Message и НЕ CallbackQuery в нашем patched-модуле.
    sysevent = SimpleNamespace(from_user=_StubUser(42))

    r1 = await mw(_ok_handler, sysevent, {})
    r2 = await mw(_ok_handler, sysevent, {})

    assert r1 == "executed"
    assert r2 == "executed"  # никакого блокирования


# ─────────────────────────────────────────────────────────────────────────
# Message в cooldown'е блокируется БЕЗ shows-alert
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_inside_cooldown_is_silently_dropped() -> None:
    mw = ThrottlingMiddleware(cooldown=2.0)
    m1 = _StubMessage(uid=42)
    m2 = _StubMessage(uid=42)

    r1 = await mw(_ok_handler, m1, {})
    r2 = await mw(_ok_handler, m2, {})

    assert r1 == "executed"
    assert r2 is None  # заблокировано
    # У Message нет .answer() — middleware просто не вызывает handler.
    # Это поведение — анти-шум для FREE-юзеров, спамящих текстом.


# ─────────────────────────────────────────────────────────────────────────
# По истечении cooldown'а событие проходит
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_passes_after_cooldown_elapsed() -> None:
    """Эмулируем «время прошло»: вручную откатываем запись в
    ``_LAST_CALL_AT``. Это эквивалентно ситуации, когда юзер ждал
    cooldown и пришёл снова — без хрупкой подмены ``time.monotonic``
    (который pytest-asyncio тоже использует для своего loop'а)."""

    import time as _time

    mw = ThrottlingMiddleware(cooldown=2.0)
    cb1 = _StubCallback(uid=42)
    cb2 = _StubCallback(uid=42)
    cb3 = _StubCallback(uid=42)

    r1 = await mw(_ok_handler, cb1, {})  # passes
    r2 = await mw(_ok_handler, cb2, {})  # blocked: ноль времени прошло

    # Симулируем «выдержку» 10 секунд: откатываем last_call ровно в прошлое.
    throttle_mod._LAST_CALL_AT[42] = _time.monotonic() - 10.0
    r3 = await mw(_ok_handler, cb3, {})  # passes — cooldown истёк

    assert r1 == "executed"
    assert r2 is None
    assert cb2.answers and cb2.answers[0][1] is False  # мягкая плашка
    assert r3 == "executed"
