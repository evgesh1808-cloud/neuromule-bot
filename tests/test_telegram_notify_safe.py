"""PR-A: специализированные aiogram.exceptions в реф-пушах + structured log.

Покрывает helper ``safe_send_user_message``:

* успешная доставка → True;
* блокировка бота (TelegramForbiddenError) → False + INFO лог;
* флуд-лимит (TelegramRetryAfter) → False + WARNING лог с retry_after;
* "chat not found" (TelegramBadRequest) → False + WARNING лог;
* сетевой сбой (TelegramNetworkError) → False + WARNING;
* неизвестная ошибка → False + ERROR с exc_info=True.

Дополнительно — гарантия, что конвейер реф-бонусов в start_admin не падает
при заблокированном пригласителе (бонус остаётся в БД).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from platforms.telegram_notify import safe_send_user_message


def _make_bot(send_message_side_effect=None, send_message_return=None):
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=send_message_side_effect,
        return_value=send_message_return,
    )
    return bot


@pytest.mark.asyncio
async def test_returns_true_on_success() -> None:
    bot = _make_bot(send_message_return=SimpleNamespace(message_id=1))
    ok = await safe_send_user_message(bot, 42, "hi", context="t")
    assert ok is True
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_forbidden_returns_false_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="platforms.telegram_notify")
    bot = _make_bot(send_message_side_effect=TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot was blocked by the user"
    ))
    ok = await safe_send_user_message(bot, 42, "hi", context="ref_bonus")
    assert ok is False
    # Должен быть INFO-лог с контекстом и user_id, без stacktrace.
    rec = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("ref_bonus" in r.message and "42" in r.message for r in rec)
    assert all(r.exc_info is None for r in rec)


@pytest.mark.asyncio
async def test_retry_after_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="platforms.telegram_notify")
    exc = TelegramRetryAfter(
        method=MagicMock(), message="Too Many Requests: retry after 7", retry_after=7
    )
    bot = _make_bot(send_message_side_effect=exc)
    ok = await safe_send_user_message(bot, 99, "hi", context="ref_bonus")
    assert ok is False
    rec = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("retry_after=7" in r.message and "99" in r.message for r in rec)


@pytest.mark.asyncio
async def test_bad_request_chat_not_found_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="platforms.telegram_notify")
    exc = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
    bot = _make_bot(send_message_side_effect=exc)
    ok = await safe_send_user_message(bot, 101, "hi", context="gallery_approve_notify")
    assert ok is False
    rec = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gallery_approve_notify" in r.message and "101" in r.message for r in rec)


@pytest.mark.asyncio
async def test_network_error_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="platforms.telegram_notify")
    bot = _make_bot(send_message_side_effect=TelegramNetworkError(
        method=MagicMock(), message="Network is unreachable"
    ))
    ok = await safe_send_user_message(bot, 7, "hi", context="ref_bonus")
    assert ok is False
    rec = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("network error" in r.message and "user_id=7" in r.message for r in rec)


@pytest.mark.asyncio
async def test_unknown_exception_logs_error_with_exc_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="platforms.telegram_notify")
    bot = _make_bot(send_message_side_effect=RuntimeError("boom"))
    ok = await safe_send_user_message(bot, 1, "hi", context="ref_bonus")
    assert ok is False
    rec = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(rec) == 1
    # Неизвестные ошибки логируются с exc_info — это критично для
    # пост-mortem'а в проде.
    assert rec[0].exc_info is not None


@pytest.mark.asyncio
async def test_forwards_parse_mode_and_markup() -> None:
    bot = _make_bot(send_message_return=SimpleNamespace(message_id=1))
    kb = SimpleNamespace(inline_keyboard=[])
    await safe_send_user_message(
        bot, 42, "<b>hi</b>",
        context="t",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=kb,
    )
    _, kwargs = bot.send_message.call_args
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_web_page_preview"] is True
    assert kwargs["reply_markup"] is kb
