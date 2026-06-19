"""Тесты TOS-gate (Telegra.ph оферта/политика/подписка).

Покрывают:
  • module-level URL-константы в config (`URL_PUBLIC_OFFER` и т.д.);
  • сервис ``services.tos`` (``is_tos_accepted`` / ``accept_tos``);
  * текст приветствия со встроенными гиперссылками на 3 документа;
  * наличие callback-константы ``accept_legal_tos`` и кнопки принятия.
"""

from __future__ import annotations

import pytest

import config as cfg
from content import messages as msg
from services import tos


# ─── config: 3 URL-константы Telegra.ph ────────────────────────────────────


def test_config_exposes_three_legal_url_constants() -> None:
    # ТЗ требует строго именованные алиасы на module-уровне config.
    assert cfg.URL_PUBLIC_OFFER == cfg.settings.service_offer_url
    assert cfg.URL_PRIVACY_POLICY == cfg.settings.privacy_policy_url
    assert cfg.URL_SUBSCRIPTION_TERMS == cfg.settings.subscription_terms_url

    # И сами URL — действительно Telegra.ph (а не пустые).
    for url in (cfg.URL_PUBLIC_OFFER, cfg.URL_PRIVACY_POLICY, cfg.URL_SUBSCRIPTION_TERMS):
        assert url.startswith("https://telegra.ph"), url


# ─── messages: текст-карточка приветствия с 3 ссылками ─────────────────────


def test_tos_welcome_gate_contains_three_hyperlinks() -> None:
    text = msg.TXT_TOS_WELCOME_GATE.format(
        offer_url=cfg.URL_PUBLIC_OFFER,
        privacy_url=cfg.URL_PRIVACY_POLICY,
        subscription_url=cfg.URL_SUBSCRIPTION_TERMS,
    )
    # Все три ссылки встроены HTML <a href=...>.
    assert f'href="{cfg.URL_PUBLIC_OFFER}"' in text
    assert f'href="{cfg.URL_PRIVACY_POLICY}"' in text
    assert f'href="{cfg.URL_SUBSCRIPTION_TERMS}"' in text
    # Семантика гейта: упоминание трёх документов.
    assert "Публичная оферта" in text
    assert "Политика конфиденциальности" in text
    assert "Условия регулярных платежей" in text


def test_tos_callback_constant_matches_specification() -> None:
    # Точное значение из ТЗ — на нём завязан handler-фильтр.
    assert msg.CB_ACCEPT_LEGAL_TOS == "accept_legal_tos"
    assert msg.TXT_TOS_ACCEPT_BTN.startswith("✅")


# ─── services.tos: атомарное принятие ──────────────────────────────────────


@pytest.mark.asyncio
async def test_is_tos_accepted_false_for_new_user(repo_module) -> None:
    # Для незарегистрированного юзера флаг по умолчанию False — это
    # критически важно: иначе шлагбаум TOS никогда не покажется.
    assert await tos.is_tos_accepted(123_456_789) is False


@pytest.mark.asyncio
async def test_accept_tos_flips_flag_idempotently(repo_module) -> None:
    user_id = 555_001
    assert await tos.is_tos_accepted(user_id) is False
    await tos.accept_tos(user_id)
    assert await tos.is_tos_accepted(user_id) is True
    # Повторный accept не ломается и оставляет True (идемпотентно).
    await tos.accept_tos(user_id)
    assert await tos.is_tos_accepted(user_id) is True
