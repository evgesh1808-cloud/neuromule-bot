"""Безопасная сборка HTML для /start (channel_url в href)."""

from __future__ import annotations

from content import messages as msg
from services.use_cases.start_ui_turn import format_start_message_html


def test_format_start_message_html_escapes_ampersand_in_url() -> None:
    kw = {
        "channel_url": "https://t.me/c/123/45?thread=1&sort=2",
        "text_daily_limit": 30,
        "photo_daily_limit": 3,
    }
    out = format_start_message_html(msg.TXT_START_FIRST_MEET_OK, kw)
    assert "thread=1&amp;sort=2" in out
    assert "href=" in out
    assert "<script>" not in out


def test_format_start_message_html_plain_telegram_url_unchanged() -> None:
    kw = {
        "channel_url": "https://t.me/mulendeeva_ai",
        "text_daily_limit": 30,
        "photo_daily_limit": 3,
    }
    out = format_start_message_html(msg.TXT_START_FIRST_MEET_NEED_CHANNEL_2, kw)
    assert 'href="https://t.me/mulendeeva_ai"' in out
