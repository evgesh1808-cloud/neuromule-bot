"""Тесты ежедневного мониторинга баланса OpenRouter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import api_balance_monitor as mon
from tests.conftest import TEST_ADMIN_IDS

_MSK = timezone(timedelta(hours=3))


def test_resolve_balance_report_admin_id_prefers_admin_telegram_id() -> None:
    from config import Settings

    cfg = Settings(
        tg_token="x",
        admin_telegram_id=777001,
        admin_ids=list(TEST_ADMIN_IDS),
    )
    assert mon.resolve_balance_report_admin_id(cfg) == 777001


def test_resolve_balance_report_admin_id_falls_back_to_first_admin_ids() -> None:
    from config import Settings

    cfg = Settings(tg_token="x", admin_ids=list(TEST_ADMIN_IDS))
    assert mon.resolve_balance_report_admin_id(cfg) == TEST_ADMIN_IDS[0]


def test_format_api_balance_report_markdown_warn() -> None:
    text = mon.format_api_balance_report_markdown(
        openrouter=mon.ProviderBalanceSnapshot("OpenRouter", 2.0, True),
        generated_at=datetime(2026, 5, 27, 9, 0, tzinfo=_MSK),
    )
    assert "⚠️" in text and "OpenRouter" in text
    assert "Низкий баланс OpenRouter" in text


def test_format_api_balance_report_markdown_ok() -> None:
    text = mon.format_api_balance_report_markdown(
        openrouter=mon.ProviderBalanceSnapshot("OpenRouter", 5.0, True),
        generated_at=datetime(2026, 5, 27, 9, 0, tzinfo=_MSK),
    )
    assert "✅" in text
    assert "Баланс OpenRouter в норме" in text


def _mock_aiohttp_response(*, status: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=text)
    resp.json = AsyncMock(return_value=json_data or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.mark.asyncio
async def test_fetch_openrouter_balance_from_credits() -> None:
    session = MagicMock()
    session.get = MagicMock(
        return_value=_mock_aiohttp_response(
            status=200,
            json_data={"data": {"total_credits": 10.0, "total_usage": 4.25}},
        )
    )
    snap = await mon.fetch_openrouter_balance(session, api_key="or-key")
    assert snap.ok is True
    assert snap.balance_usd == pytest.approx(5.75)


@pytest.mark.asyncio
async def test_fetch_openrouter_balance_fallback_to_key_limit() -> None:
    credits_resp = _mock_aiohttp_response(status=401, text="unauthorized")
    key_resp = _mock_aiohttp_response(
        status=200,
        json_data={"data": {"limit_remaining": 2.5}},
    )
    session = MagicMock()
    session.get = MagicMock(side_effect=[credits_resp, key_resp])

    snap = await mon.fetch_openrouter_balance(session, api_key="or-key")
    assert snap.ok is True
    assert snap.balance_usd == 2.5
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_send_daily_api_balance_report_sends_markdown() -> None:
    from config import Settings

    cfg = Settings(
        tg_token="123:ABC",
        admin_telegram_id=TEST_ADMIN_IDS[0],
    )
    fake_bot = AsyncMock()
    fake_bot.session.close = AsyncMock()

    with (
        patch.object(mon, "build_api_balance_report", AsyncMock(return_value="📊 report")),
        patch("services.api_balance_monitor.Bot", return_value=fake_bot),
    ):
        await mon.send_daily_api_balance_report(cfg)

    fake_bot.send_message.assert_awaited_once()
    kwargs = fake_bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == TEST_ADMIN_IDS[0]
    assert kwargs["text"] == "📊 report"


def test_seconds_until_next_msk_clock_before_noon() -> None:
    now = datetime(2026, 5, 27, 8, 30, tzinfo=_MSK)
    delay = mon._seconds_until_next_msk_clock(9, 0, now=now)
    assert delay == pytest.approx(30 * 60, rel=0.01)
