"""Юнит-тесты онбординг-флоу `/start` (welcome-gate + дашборд).

Тесты НЕ требуют инстанса бота, диспетчера или сети: ``message.from_user``
симулируется ``SimpleNamespace``. Зависимость от ``config.settings.admin_ids``
изолируется автофикстурой ``_isolate_settings_admin_ids`` в ``tests/conftest.py``,
которая на время каждого теста подставляет фиксированный кортеж
``TEST_ADMIN_IDS = (999111, 999222)``. Это делает тесты детерминированными
независимо от ``.env`` / CI-окружения.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config import settings
from platforms.handlers.start_onboarding import (
    build_dashboard_text,
    resolve_greeting,
)

ADMIN_LINE = "\n📱 ИИ-Панель:   В разработке ⚙️ (Скоро в Web App)"
WEBAPP_ENABLED_LINE = "\n📱 ИИ-Панель:   Доступна по кнопке 👇"


# ── resolve_greeting ──────────────────────────────────────────────────────


def test_resolve_greeting_with_first_name() -> None:
    """``first_name='Иван'`` → ровно «Привет, Иван!»."""
    user = SimpleNamespace(first_name="Иван", last_name=None, id=1)

    assert resolve_greeting(user) == "Привет, Иван!"


def test_resolve_greeting_without_first_name() -> None:
    """``first_name=None/''`` → ровно «Привет!» (без хвостовой запятой/пробела)."""
    user_none = SimpleNamespace(first_name=None, id=1)
    user_empty = SimpleNamespace(first_name="", id=1)

    assert resolve_greeting(user_none) == "Привет!"
    assert resolve_greeting(user_empty) == "Привет!"


def test_resolve_greeting_ignores_last_name() -> None:
    """``last_name`` не утекает в приветствие — используется только ``first_name``."""
    user = SimpleNamespace(first_name="Иван", last_name="Петров", id=1)

    greeting = resolve_greeting(user)

    assert greeting == "Привет, Иван!"
    assert "Петров" not in greeting


# ── build_dashboard_text · админ-строка ───────────────────────────────────


def test_build_dashboard_text_no_admin_line_for_regular_user() -> None:
    """Для НЕ-админа дашборд НЕ содержит ни маркера «ИИ-Панель», ни «В разработке».

    Гвард на ``-1 not in admin_ids`` ловит случай, когда кто-то по ошибке
    впишет ``-1`` в тестовую конфигурацию: тест честно покраснеет здесь,
    а не на главном утверждении, что даст понятный диагноз.
    """
    non_admin_id = -1

    assert non_admin_id not in tuple(settings.admin_ids), (
        "Гвард: id=-1 не должен входить в settings.admin_ids; "
        "иначе кейс «НЕ-админ» теряет смысл."
    )

    user = SimpleNamespace(first_name="Иван", id=non_admin_id)
    text = build_dashboard_text(user)

    assert "ИИ-Панель" not in text
    assert "В разработке" not in text


def test_build_dashboard_text_appends_admin_line_for_admin() -> None:
    """Для админа строка ИИ-Панели дописана в тело дашборда."""

    admin_ids = tuple(settings.admin_ids)
    if not admin_ids:
        pytest.skip("Конфигурация admin_ids пуста, пропускаем тест")

    user = SimpleNamespace(first_name="Иван", id=admin_ids[0])
    text = build_dashboard_text(user)

    assert ADMIN_LINE in text
    assert "⚡️ Энергия:" in text


# ── build_dashboard_text · WebApp-режим (is_webapp_enabled=True) ──────────


def test_build_dashboard_text_webapp_enabled_visible_to_regular_user() -> None:
    """``is_webapp_enabled=True`` → ВСЕ юзеры видят «Доступна по кнопке 👇».

    Подменяем флаг локально через ``object.__setattr__`` (обходит frozen
    pydantic). НЕ-админ всё равно получает строку — это и есть отличие от
    rollout-режима, где не-админу строка вовсе скрыта.
    """
    object.__setattr__(settings, "is_webapp_enabled", True)
    non_admin_id = -1
    assert non_admin_id not in tuple(settings.admin_ids)

    user = SimpleNamespace(first_name="Иван", id=non_admin_id)
    text = build_dashboard_text(user)

    assert WEBAPP_ENABLED_LINE in text
    assert "В разработке" not in text


def test_build_dashboard_text_webapp_enabled_overrides_admin_stub() -> None:
    """``is_webapp_enabled=True`` побеждает админ-заглушку.

    Даже для админа отображается «production»-строка про доступную кнопку,
    а не заглушка «В разработке ⚙️».
    """
    object.__setattr__(settings, "is_webapp_enabled", True)
    admin_ids = tuple(settings.admin_ids)
    if not admin_ids:
        pytest.skip("Конфигурация admin_ids пуста, пропускаем тест")

    user = SimpleNamespace(first_name="Иван", id=admin_ids[0])
    text = build_dashboard_text(user)

    assert WEBAPP_ENABLED_LINE in text
    assert ADMIN_LINE not in text
