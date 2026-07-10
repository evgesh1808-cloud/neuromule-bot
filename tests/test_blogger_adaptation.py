"""Тесты адаптации поста блогера под площадки СНГ."""

from __future__ import annotations

from content import messages as msg
from services.blogger_adaptation import (
    parse_adapt_target,
    prepare_adapted_telegram_html,
)


def test_parse_adapt_target_valid() -> None:
    assert parse_adapt_target(msg.CB_ADAPT_TARGET_VIDEO) == "video"
    assert parse_adapt_target(msg.CB_ADAPT_TARGET_VC) == "vc"
    assert parse_adapt_target(msg.CB_ADAPT_TARGET_VK) == "vk"
    assert parse_adapt_target(msg.CB_ADAPT_TARGET_TG_MAX) == "tg_max"
    assert parse_adapt_target("adapt_target:unknown") is None


def test_prepare_adapted_telegram_html_repairs_markdown_and_closes_b() -> None:
    raw = "**жирный** тезис и <b>незакрытый"
    html = prepare_adapted_telegram_html(raw)
    assert "<b>жирный</b>" in html
    assert html.count("<b>") == html.lower().count("</b>")
