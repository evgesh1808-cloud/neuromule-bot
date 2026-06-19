"""Тесты сервиса отзывов NeuroMule 🐎⚡️ (services/reviews_service.py).

Покрывают:
  • инициализация схемы ``user_reviews``;
  • вставка отзыва со статусом ``pending``;
  • атомарное начисление бонуса ``+5 ⚡`` на ``energy_paid``;
  • approve / reject через ``set_review_status``;
  • ``get_review`` возвращает корректный набор полей.
"""

from __future__ import annotations

import aiosqlite
import pytest

from services import reviews_service


@pytest.mark.asyncio
async def test_submit_review_and_grant_bonus_atomic(repo_module) -> None:
    user_id = 9001
    await repo_module.ensure_user(user_id, username="muletester")

    review_id = await reviews_service.submit_review(
        user_id,
        kind="text",
        content="Космос! ИИ дал крутой совет",
    )
    assert review_id > 0

    bonus_ok = await reviews_service.grant_review_bonus(user_id, amount=5)
    assert bonus_ok is True

    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        async with db.execute(
            "SELECT energy_paid, energy, balance_energy FROM users WHERE id=?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    energy_paid, energy, balance_energy = row
    assert int(energy_paid) >= 5
    # legacy mirror-поля синхронны
    assert int(energy) == int(balance_energy)


@pytest.mark.asyncio
async def test_grant_review_bonus_rejects_non_positive(repo_module) -> None:
    user_id = 9002
    await repo_module.ensure_user(user_id)
    assert await reviews_service.grant_review_bonus(user_id, amount=0) is False
    assert await reviews_service.grant_review_bonus(user_id, amount=-3) is False


@pytest.mark.asyncio
async def test_get_review_and_set_status_round_trip(repo_module) -> None:
    user_id = 9003
    await repo_module.ensure_user(user_id)
    review_id = await reviews_service.submit_review(
        user_id, kind="text", content="мощно"
    )

    review = await reviews_service.get_review(review_id)
    assert review is not None
    assert review["user_id"] == user_id
    assert review["kind"] == "text"
    assert review["status"] == "pending"
    assert review["content"] == "мощно"

    assert await reviews_service.set_review_status(review_id, "approved") is True
    refreshed = await reviews_service.get_review(review_id)
    assert refreshed is not None
    assert refreshed["status"] == "approved"
    assert refreshed["moderated_at"] is not None


@pytest.mark.asyncio
async def test_submit_review_supports_media(repo_module) -> None:
    user_id = 9004
    await repo_module.ensure_user(user_id)
    review_id = await reviews_service.submit_review(
        user_id,
        kind="photo",
        content="скрин Imagen 4",
        file_id="AgACAgIAAxk_test",
    )
    review = await reviews_service.get_review(review_id)
    assert review is not None
    assert review["kind"] == "photo"
    assert review["file_id"] == "AgACAgIAAxk_test"


@pytest.mark.asyncio
async def test_get_review_returns_none_for_unknown(repo_module) -> None:
    assert await reviews_service.get_review(999_999_999) is None
