"""Нативная кнопка Studio (MenuButtonWebApp URL)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from platforms.telegram_studio import resolve_studio_webapp_url, setup_studio_menu_button


def test_resolve_studio_prefers_studio_url(monkeypatch) -> None:
    from config import settings

    object.__setattr__(settings, "webapp_studio_url", "https://studio.example/app/")
    object.__setattr__(settings, "webapp_shop_url", "https://shop.example/")
    assert resolve_studio_webapp_url() == "https://studio.example/app/"


def test_resolve_studio_falls_back_to_table_base(monkeypatch) -> None:
    from config import settings

    object.__setattr__(settings, "webapp_studio_url", None)
    object.__setattr__(settings, "webapp_shop_url", None)
    object.__setattr__(
        settings,
        "webapp_table_reports_url",
        "https://user.github.io/neuromule-table/?report_id={report_id}",
    )
    assert resolve_studio_webapp_url() == "https://user.github.io/neuromule-table/"


def test_setup_studio_menu_button_calls_telegram(monkeypatch) -> None:
    from config import settings

    object.__setattr__(settings, "webapp_studio_url", "https://studio.example/app")
    bot = MagicMock()
    bot.set_chat_menu_button = AsyncMock()

    ok = asyncio.run(setup_studio_menu_button(bot))
    assert ok is True
    bot.set_chat_menu_button.assert_awaited_once()
    call_kw = bot.set_chat_menu_button.await_args.kwargs
    assert call_kw["menu_button"].text == "📱 Studio"
    assert call_kw["menu_button"].web_app.url == "https://studio.example/app/"
