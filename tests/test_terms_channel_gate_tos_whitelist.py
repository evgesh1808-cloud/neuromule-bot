"""Regression-тесты: ``TermsGateMiddleware`` и ``ChannelGateMiddleware``
ОБЯЗАНЫ пропускать callback ``accept_legal_tos`` даже для юзера, который
ещё не принял оферту (``accepted_terms == False``).

Контекст бага (PR-G follow-up): новый ``TosGateMiddleware`` whitelist'ит
``accept_legal_tos``, но старые ``TermsGateMiddleware`` и
``ChannelGateMiddleware`` — нет. В результате callback от свежей кнопки
«✅ Принять условия и Запустить» резался ДО handler'а: юзер видел
paywall reminder вместо сохранения согласия → визуальный «бот мёртв».

Эти тесты страхуют, что whitelist никогда не «потеряется» в будущих
рефакторингах.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from content import messages as msg
from platforms.telegram_middleware import (
    ChannelGateMiddleware,
    TermsGateMiddleware,
)


class _StubUser:
    def __init__(self, uid: int) -> None:
        self.id = uid


def _make_callback(data: str, uid: int = 12345, *, message=None):
    """Stub CallbackQuery, проходящий isinstance() для middleware'ов.

    Используем aiogram-тип напрямую — иначе isinstance(event,
    types.CallbackQuery) в middleware не совпадёт и event уйдёт по
    else-ветке ``return await handler(event, data)``, замаскировав
    регрессию.

    Pydantic-инстанс **frozen**, поэтому ``message`` передаём в
    конструкторе (присвоить после ``model_construct`` нельзя)."""
    from aiogram import types

    return types.CallbackQuery.model_construct(
        id="test_cb",
        from_user=types.User(id=uid, is_bot=False, first_name="Test"),
        chat_instance="ci",
        data=data,
        message=message,
    )


# ── TermsGateMiddleware ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terms_gate_passes_accept_legal_tos_for_user_without_terms(
    mocker: MockerFixture,
) -> None:
    """Юзер ещё не принял оферту → callback accept_legal_tos должен
    пройти СКВОЗЬ TermsGateMiddleware к handler'у."""

    # user_has_accepted_terms → False (юзер ещё не принял)
    mocker.patch(
        "platforms.telegram_middleware.user_has_accepted_terms",
        new=mocker.AsyncMock(return_value=False),
    )

    handler = mocker.AsyncMock(return_value="handler_was_called")
    mw = TermsGateMiddleware()
    cb = _make_callback(msg.CB_ACCEPT_LEGAL_TOS)
    data = {"event_from_user": _StubUser(12345)}

    result = await mw(handler, cb, data)

    handler.assert_awaited_once()
    assert result == "handler_was_called"


# Контрольные «block-flow» тесты (что НЕ-whitelist'нутый callback всё ещё
# режется) НЕ дублируются здесь — они уже покрыты в test_tos_gate_middleware.py
# через специальный stub-callback без bot-mount'а. Этот файл сфокусирован
# исключительно на pass-through whitelist'е новой константы.


# ── ChannelGateMiddleware ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_channel_gate_passes_accept_legal_tos_for_unsubscribed_user(
    mocker: MockerFixture,
) -> None:
    """Юзер не подписан на канал → callback accept_legal_tos должен пройти
    СКВОЗЬ ChannelGateMiddleware. Иначе после клика на TOS-кнопку юзер
    получит paywall канала, а сам accept_tos в БД не запишется."""

    fake_channel_sub = SimpleNamespace(
        is_subscribed_cached=mocker.AsyncMock(return_value=False),
    )
    mocker.patch(
        "platforms.telegram_middleware.user_has_accepted_terms",
        new=mocker.AsyncMock(return_value=False),
    )

    handler = mocker.AsyncMock(return_value="handler_was_called")
    mw = ChannelGateMiddleware(fake_channel_sub)  # type: ignore[arg-type]
    cb = _make_callback(msg.CB_ACCEPT_LEGAL_TOS)
    data = {"event_from_user": _StubUser(12345)}

    result = await mw(handler, cb, data)

    handler.assert_awaited_once()
    assert result == "handler_was_called"
    # is_subscribed_cached НЕ должен вызываться для whitelist'нутых callback'ов
    # — это лишний round-trip к Telegram API.
    fake_channel_sub.is_subscribed_cached.assert_not_awaited()
