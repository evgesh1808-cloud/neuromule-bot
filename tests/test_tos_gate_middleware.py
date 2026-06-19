"""PR-F: интеграционные тесты TosGateMiddleware.

Проверяет ВСЕ ветки шлагбаума:

* whitelist: ``/start`` и callback ``accept_legal_tos`` всегда проходят;
* PreCheckoutQuery — системный, пропускается без проверки TOS;
* юзер без TOS:
    - Message → handler НЕ вызван, отрисован gate-сообщение;
    - CallbackQuery → handler НЕ вызван, callback.answer + gate-сообщение;
    - InlineQuery → пустой результат, handler НЕ вызван;
* юзер с TOS → handler вызван штатно.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from platforms import telegram_tos_gate as tos_mod
from platforms.telegram_tos_gate import TosGateMiddleware


# ── Лёгкие stub'ы, подменяющие aiogram-типы внутри middleware ────────────


class _StubUser:
    def __init__(self, uid: int) -> None:
        self.id = uid


class _StubMessage:
    def __init__(self, uid: int, text: str = "") -> None:
        self.from_user = _StubUser(uid) if uid else None
        self.text = text
        self.answer = None  # AsyncMock проставит фикстура


class _StubCallback:
    def __init__(self, uid: int, data: str = "x") -> None:
        self.from_user = _StubUser(uid)
        self.data = data
        self.message = _StubMessage(uid)
        self.answer = None


class _StubInlineQuery:
    def __init__(self, uid: int) -> None:
        self.from_user = _StubUser(uid)
        self.answer = None


class _StubPreCheckoutQuery:
    def __init__(self, uid: int) -> None:
        self.from_user = _StubUser(uid)


@pytest.fixture(autouse=True)
def _patch_aiogram_types(mocker: MockerFixture):
    """Подменяем aiogram-классы внутри middleware, чтобы isinstance работал
    с нашими stub'ами (тесты не зависят от поднятия aiogram-Dispatcher'а)."""
    mocker.patch.object(tos_mod, "Message", _StubMessage)
    mocker.patch.object(tos_mod, "CallbackQuery", _StubCallback)
    mocker.patch.object(tos_mod, "InlineQuery", _StubInlineQuery)
    mocker.patch.object(tos_mod, "PreCheckoutQuery", _StubPreCheckoutQuery)


async def _noop_handler(event, data):
    return "executed"


def _patch_tos(mocker: MockerFixture, *, accepted: bool) -> None:
    mocker.patch.object(
        tos_mod, "is_tos_accepted", new=mocker.AsyncMock(return_value=accepted)
    )


# ─────────────────────────────────────────────────────────────────────────
# 1. whitelist: /start всегда проходит
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_command_passes_without_tos(mocker: MockerFixture) -> None:
    _patch_tos(mocker, accepted=False)
    mw = TosGateMiddleware()
    event = _StubMessage(uid=42, text="/start ref_xyz")
    event.answer = mocker.AsyncMock()

    result = await mw(_noop_handler, event, {})

    assert result == "executed"  # handler вызван
    event.answer.assert_not_called()  # gate-карточка НЕ показана


@pytest.mark.asyncio
async def test_accept_tos_callback_passes_without_tos(
    mocker: MockerFixture,
) -> None:
    from content import messages as msg

    _patch_tos(mocker, accepted=False)
    mw = TosGateMiddleware()
    cb = _StubCallback(uid=42, data=msg.CB_ACCEPT_LEGAL_TOS)
    cb.answer = mocker.AsyncMock()
    cb.message.answer = mocker.AsyncMock()

    result = await mw(_noop_handler, cb, {})

    assert result == "executed"
    cb.answer.assert_not_called()
    cb.message.answer.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# 2. PreCheckoutQuery — системный, пропускается
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pre_checkout_query_bypasses_tos_check(
    mocker: MockerFixture,
) -> None:
    is_tos_mock = mocker.patch.object(
        tos_mod, "is_tos_accepted", new=mocker.AsyncMock(return_value=False)
    )
    mw = TosGateMiddleware()
    event = _StubPreCheckoutQuery(uid=42)

    result = await mw(_noop_handler, event, {})

    assert result == "executed"
    # is_tos_accepted даже не должен быть вызван — PreCheckoutQuery идёт мимо.
    is_tos_mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# 3. Без TOS: Message блокируется
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_without_tos_is_blocked_and_renders_gate(
    mocker: MockerFixture,
) -> None:
    from content import messages as msg

    _patch_tos(mocker, accepted=False)
    handler_spy = mocker.AsyncMock()
    mw = TosGateMiddleware()
    event = _StubMessage(uid=42, text="привет")
    event.answer = mocker.AsyncMock()

    result = await mw(handler_spy, event, {})

    assert result is None
    handler_spy.assert_not_called()  # БИЗНЕС-логика НЕ запущена
    event.answer.assert_awaited_once()
    args, kwargs = event.answer.call_args
    assert kwargs.get("parse_mode") == "HTML"
    # Gate-текст должен содержать ключевые маркеры карточки.
    rendered = args[0]
    assert "NeuroMule" in rendered
    assert msg.TXT_TOS_ACCEPT_BTN in str(kwargs["reply_markup"].inline_keyboard)


# ─────────────────────────────────────────────────────────────────────────
# 4. Без TOS: CallbackQuery блокируется
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_without_tos_shows_alert_and_gate(
    mocker: MockerFixture,
) -> None:
    _patch_tos(mocker, accepted=False)
    handler_spy = mocker.AsyncMock()
    mw = TosGateMiddleware()
    cb = _StubCallback(uid=42, data="make_video")
    cb.answer = mocker.AsyncMock()
    cb.message.answer = mocker.AsyncMock()

    result = await mw(handler_spy, cb, {})

    assert result is None
    handler_spy.assert_not_called()
    # Сначала — короткий alert с просьбой пойти в /start.
    cb.answer.assert_awaited_once()
    _, alert_kwargs = cb.answer.call_args
    assert alert_kwargs.get("show_alert") is True
    # Затем — полноценная gate-карточка в чат.
    cb.message.answer.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────
# 5. Без TOS: InlineQuery возвращает пустой результат
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_query_without_tos_returns_empty(
    mocker: MockerFixture,
) -> None:
    _patch_tos(mocker, accepted=False)
    handler_spy = mocker.AsyncMock()
    mw = TosGateMiddleware()
    iq = _StubInlineQuery(uid=42)
    iq.answer = mocker.AsyncMock()

    result = await mw(handler_spy, iq, {})

    assert result is None
    handler_spy.assert_not_called()
    iq.answer.assert_awaited_once()
    _, kwargs = iq.answer.call_args
    assert kwargs.get("results") == []
    assert kwargs.get("is_personal") is True


# ─────────────────────────────────────────────────────────────────────────
# 6. С TOS — handler штатно вызывается
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accepted_tos_passes_message_to_handler(
    mocker: MockerFixture,
) -> None:
    _patch_tos(mocker, accepted=True)
    mw = TosGateMiddleware()
    event = _StubMessage(uid=42, text="any text")
    event.answer = mocker.AsyncMock()

    result = await mw(_noop_handler, event, {})

    assert result == "executed"
    event.answer.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# 7. Аноним без from_user (например, channel_post) — пропускаем
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_without_user_passes_through(mocker: MockerFixture) -> None:
    """Если у апдейта нет from_user — мы не знаем, кого блокировать."""
    is_tos_mock = mocker.patch.object(
        tos_mod, "is_tos_accepted", new=mocker.AsyncMock(return_value=False)
    )
    mw = TosGateMiddleware()
    event = _StubMessage(uid=0)
    event.answer = mocker.AsyncMock()

    result = await mw(_noop_handler, event, {})

    assert result == "executed"
    is_tos_mock.assert_not_called()  # быстрый exit ДО async-вызова
