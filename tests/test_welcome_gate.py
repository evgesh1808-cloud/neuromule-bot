"""Юнит-тесты онбординг-флоу `/start` (welcome-gate + экран активации)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config import settings
from content import messages as msg
from platforms.handlers.start_onboarding import (
    build_dashboard_text,
    resolve_greeting,
)

STUDIO_LINE = msg.BTN_STUDIO_MENU


def test_resolve_greeting_with_first_name() -> None:
    user = SimpleNamespace(first_name="Иван", last_name=None, id=1)
    assert resolve_greeting(user) == "Привет, Иван!"


def test_resolve_greeting_without_first_name() -> None:
    user_none = SimpleNamespace(first_name=None, id=1)
    user_empty = SimpleNamespace(first_name="", id=1)
    assert resolve_greeting(user_none) == "Привет!"
    assert resolve_greeting(user_empty) == "Привет!"


def test_resolve_greeting_ignores_last_name() -> None:
    user = SimpleNamespace(first_name="Иван", last_name="Петров", id=1)
    greeting = resolve_greeting(user)
    assert greeting == "Привет, Иван!"
    assert "Петров" not in greeting


def test_build_dashboard_text_includes_studio_hint() -> None:
    user = SimpleNamespace(first_name="Иван", id=-1)
    assert -1 not in tuple(settings.admin_ids)
    text = build_dashboard_text(user)
    assert STUDIO_LINE in text
    assert "ИИ-Панель" not in text
    assert "В разработке" not in text


def test_build_dashboard_text_studio_for_admin() -> None:
    admin_ids = tuple(settings.admin_ids)
    if not admin_ids:
        pytest.skip("Конфигурация admin_ids пуста, пропускаем тест")
    user = SimpleNamespace(first_name="Иван", id=admin_ids[0])
    text = build_dashboard_text(user)
    assert STUDIO_LINE in text
    assert "⚡️ Энергия:" in text
