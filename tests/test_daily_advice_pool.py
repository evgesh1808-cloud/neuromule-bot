"""Пул «Совета дня»: assemble, ключи HD, stale fallback."""

from __future__ import annotations

import pytest

from services import daily_advice_pool as pool
from services.daily_advice_pool import (
    assemble_daily_advice_from_pool,
    resolve_hd_pool_key,
)


def test_resolve_hd_pool_key_five_types() -> None:
    assert resolve_hd_pool_key("Генератор") == "generator"
    assert resolve_hd_pool_key("Манифестирующий Генератор") == "mg"
    assert resolve_hd_pool_key("МГ") == "mg"
    assert resolve_hd_pool_key("Манифестор") == "manifestor"
    assert resolve_hd_pool_key("Проектор") == "projector"
    assert resolve_hd_pool_key("Рефлектор") == "reflector"
    assert resolve_hd_pool_key("НЕ ОПРЕДЕЛЕН") == "generator"
    assert resolve_hd_pool_key("") == "generator"


def test_assemble_substitutes_placeholders() -> None:
    row = {
        "barometer": "Сегодня мягкий день для всех.",
        "navigator": (
            "{display_name}, тип в роли {user_role}: опора на "
            "{birth_date} {birth_time}, {birth_place}."
        ),
        "step_plus": "Сделай паузу, {display_name}.",
        "energy_drain": "Не спорь из роли {user_role}.",
    }
    text = assemble_daily_advice_from_pool(
        row,
        display_name="Женя",
        birth_date="14.05.1990",
        birth_time="14:35",
        birth_place="Москва",
        user_role="предприниматель",
        cta_text="CTA_LINE",
    )
    assert "Женя" in text
    assert "14.05.1990" in text
    assert "14:35" in text
    assert "Москва" in text
    assert "предприниматель" in text
    assert "CTA_LINE" in text
    assert "{display_name}" not in text
    assert "ЗВЕЗДНЫЙ БАРОМЕТР" in text
    assert "ТВОЙ НАВИГАТОР" in text


def test_assemble_safe_on_broken_braces() -> None:
    row = {
        "barometer": "Ок {unknown_token} и обычный текст",
        "navigator": "{display_name} — база",
        "step_plus": "шаг",
        "energy_drain": "стоп",
    }
    text = assemble_daily_advice_from_pool(
        row,
        display_name="Аня",
        birth_date="1",
        birth_time="2",
        birth_place="3",
        user_role="4",
        cta_text="",
    )
    assert "Аня" in text
    assert "{unknown_token}" in text


@pytest.mark.asyncio
async def test_pool_cache_hit_and_yesterday_fallback(repo_module) -> None:
    today = pool.advice_date_iso_msk()
    yesterday = pool.yesterday_advice_date_iso_msk()
    await repo_module.upsert_daily_advice_pool(
        advice_date=yesterday,
        hd_type_key="projector",
        barometer="вчерашний барометр",
        navigator="{display_name} вчера {birth_place}",
        step_plus="шаг вчера",
        energy_drain="стоп вчера",
        model_id="test",
    )
    # Сегодня пусто → stale
    row = await pool.fetch_pool_with_stale_fallback("Проектор")
    assert row is not None
    assert row["barometer"] == "вчерашний барометр"

    await repo_module.upsert_daily_advice_pool(
        advice_date=today,
        hd_type_key="projector",
        barometer="сегодняшний барометр",
        navigator="{display_name} сегодня",
        step_plus="шаг сегодня",
        energy_drain="стоп сегодня",
        model_id="test",
    )
    row2 = await pool.fetch_pool_with_stale_fallback("projector")
    assert row2 is not None
    assert row2["barometer"] == "сегодняшний барометр"


@pytest.mark.asyncio
async def test_get_daily_advice_pool_roundtrip(repo_module) -> None:
    day = "2099-01-15"
    await repo_module.upsert_daily_advice_pool(
        advice_date=day,
        hd_type_key="generator",
        barometer="b",
        navigator="n {display_name}",
        step_plus="s",
        energy_drain="e",
    )
    got = await repo_module.get_daily_advice_pool(day, "generator")
    assert got is not None
    assert got["barometer"] == "b"
    assert "generator" in await repo_module.list_daily_advice_pool_keys(day)
