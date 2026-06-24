"""Клавиатура Web App для table_generator."""

from __future__ import annotations

from platforms.table_mini_app_keyboard import (
    build_table_mini_app_url,
    get_table_mini_app_keyboard,
    table_delivery_keyboard,
)
from content import messages as msg
from services.table_chart_types import ChartType


def test_build_table_mini_app_url_with_placeholder(monkeypatch) -> None:
    from config import settings

    object.__setattr__(
        settings,
        "webapp_table_reports_url",
        "https://user.github.io/neuromule/?report_id={report_id}",
    )
    object.__setattr__(settings, "mini_app_api_base_url", "")
    assert build_table_mini_app_url(42) == (
        "https://user.github.io/neuromule/?report_id=42&ui_v=20260527d"
    )


def test_build_table_mini_app_url_appends_query(monkeypatch) -> None:
    from config import settings

    object.__setattr__(
        settings,
        "webapp_table_reports_url",
        "https://user.github.io/neuromule/index.html",
    )
    object.__setattr__(settings, "mini_app_api_base_url", "")
    assert build_table_mini_app_url(7) == (
        "https://user.github.io/neuromule/index.html?report_id=7&ui_v=20260527d"
    )


def test_build_table_mini_app_url_appends_api_base(monkeypatch) -> None:
    from config import settings

    object.__setattr__(
        settings,
        "webapp_table_reports_url",
        "https://user.github.io/neuromule/?report_id={report_id}",
    )
    object.__setattr__(settings, "mini_app_api_base_url", "https://api.example.com")
    assert (
        build_table_mini_app_url(42)
        == "https://user.github.io/neuromule/?report_id=42"
        "&api_base=https://api.example.com&ui_v=20260527d"
    )


def test_get_table_mini_app_keyboard_always_with_report_id() -> None:
    kb = get_table_mini_app_keyboard(42)
    assert kb is not None
    assert kb.inline_keyboard[0][0].text == msg.BTN_MINI_APP_DASHBOARD
    assert kb.inline_keyboard[0][0].web_app is not None


def test_table_delivery_keyboard_includes_mini_app_row(monkeypatch) -> None:
    from config import settings

    object.__setattr__(
        settings,
        "webapp_table_reports_url",
        "https://user.github.io/app/?report_id={report_id}",
    )
    kb = table_delivery_keyboard(ChartType.BAR, report_id=99)
    assert len(kb.inline_keyboard) == 2
    assert kb.inline_keyboard[0][0].web_app is not None
