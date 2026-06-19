"""Вирусный Inline Mode NeuroMule 🐎⚡️: гард FREE, биллинг, рефанд."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from content import messages as msg
from platforms.handlers import inline_flow
from platforms.handlers.inline_flow import (
    INLINE_AI_TIMEOUT_SEC,
    _bot_deep_link,
    _inline_referral_keyboard,
    _result_id,
    inline_query_handler,
)
from services.billing import store
from services.billing.store import init_billing_schema, load_user_billing


# ─── фейковый InlineQuery (без aiogram-сетевого слоя) ─────────────────────


@dataclass
class _FakeUser:
    id: int
    username: str | None = "tester"


@dataclass
class _FakeInlineQuery:
    """Минимальный двойник aiogram InlineQuery для unit-тестов."""

    id: str
    query: str
    user_id: int
    answers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def from_user(self) -> _FakeUser:
        return _FakeUser(id=self.user_id)

    async def answer(self, results, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.answers.append({"results": list(results), **kwargs})


def _seed_query(uid: int, text: str = "hi") -> _FakeInlineQuery:
    return _FakeInlineQuery(id="qid-1", query=text, user_id=uid)


# ─── helpers / constants ──────────────────────────────────────────────────


def test_inline_viral_footer_contains_brand_and_separator() -> None:
    foot = msg.TXT_INLINE_VIRAL_FOOTER
    assert "@NeuroMule_bot 🐎⚡️" in foot
    assert "───────────────────" in foot
    assert "<b>" in foot and "</b>" in foot


def test_inline_referral_keyboard_links_to_bot_deeplink() -> None:
    kb = _inline_referral_keyboard()
    assert len(kb.inline_keyboard) == 1
    btn = kb.inline_keyboard[0][0]
    assert btn.text == msg.TXT_INLINE_RESULT_BTN
    assert btn.url is not None
    assert btn.url.startswith("https://t.me/")
    assert "inline_ref" in btn.url


def test_bot_deep_link_uses_settings_username() -> None:
    url = _bot_deep_link("inline_ref")
    assert url.startswith("https://t.me/")
    assert "?start=inline_ref" in url


def test_result_id_is_stable_for_same_input() -> None:
    a = _result_id(123, "hello world")
    b = _result_id(123, "hello world")
    c = _result_id(123, "hello world!")
    assert a == b
    assert a != c
    assert a.startswith("nm:123:")


def test_inline_ai_timeout_under_telegram_limit() -> None:
    # Telegram отрезает inline-ответ ~10s; нам нужно вернуть значительно раньше.
    assert INLINE_AI_TIMEOUT_SEC <= 5.0


# ─── FSM поведения (через фейк-query, без сети) ────────────────────────────


@pytest.mark.asyncio
async def test_inline_empty_query_returns_hint_stub(repo_module) -> None:
    await init_billing_schema()
    uid = 700_001
    await repo_module.ensure_user(uid)

    q = _seed_query(uid, text="   ")
    await inline_query_handler(q)  # type: ignore[arg-type]

    assert len(q.answers) == 1
    payload = q.answers[0]
    assert payload["is_personal"] is True
    assert payload["cache_time"] == 0
    article = payload["results"][0]
    assert article.title == msg.TXT_INLINE_EMPTY_TITLE


@pytest.mark.asyncio
async def test_inline_free_user_blocked_without_spend(repo_module) -> None:
    """FREE без ULTRA-семьи → жёсткая заглушка, без списания."""
    await init_billing_schema()
    uid = 700_002
    await repo_module.ensure_user(uid)
    await repo_module.set_user_tariff(uid, "FREE")

    before = await load_user_billing(uid)

    q = _seed_query(uid, text="придумай слоган")
    await inline_query_handler(q)  # type: ignore[arg-type]

    after = await load_user_billing(uid)
    assert before.energy_free == after.energy_free
    assert before.energy_paid == after.energy_paid
    assert before.crystals == after.crystals

    article = q.answers[0]["results"][0]
    assert article.title == msg.TXT_INLINE_FREE_LOCK_TITLE
    assert q.answers[0]["switch_pm_parameter"] == "inline_lock"


@pytest.mark.asyncio
async def test_inline_paid_no_balance_returns_insufficient(repo_module) -> None:
    """MINI без энергии/💎 → заглушка о нехватке, без падений."""
    await init_billing_schema()
    uid = 700_003
    await repo_module.ensure_user(uid)
    await repo_module.set_user_tariff(uid, "MINI")
    import aiosqlite

    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET energy_paid=0, energy_free=0, energy=0, "
            "balance_energy=0, sub_crystals=0, buy_crystals=0, crystals=0 "
            "WHERE id=?",
            (uid,),
        )
        await db.commit()

    q = _seed_query(uid, text="hello")
    await inline_query_handler(q)  # type: ignore[arg-type]

    article = q.answers[0]["results"][0]
    assert article.title == msg.TXT_INLINE_INSUFFICIENT_TITLE
    assert q.answers[0]["switch_pm_parameter"] == "inline_topup"


@pytest.mark.asyncio
async def test_inline_paid_success_charges_and_appends_footer(
    repo_module, monkeypatch
) -> None:
    """SMART с энергией: списывает 1 ⚡ и пришивает виральную подпись."""
    await init_billing_schema()
    uid = 700_004
    await repo_module.ensure_user(uid)
    await repo_module.set_user_tariff(uid, "SMART")
    import aiosqlite

    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET energy_paid=10, energy_free=0, energy=10, "
            "balance_energy=10, sub_crystals=0, buy_crystals=0, crystals=0 "
            "WHERE id=?",
            (uid,),
        )
        await db.commit()

    async def _fake_ai(query_text: str) -> str:
        return "Виральный <b>хук</b> для NeuroMule"

    monkeypatch.setattr(inline_flow, "_generate_inline_answer", _fake_ai)

    q = _seed_query(uid, text="придумай хук")
    await inline_query_handler(q)  # type: ignore[arg-type]

    assert len(q.answers) == 1
    article = q.answers[0]["results"][0]
    body = article.input_message_content.message_text
    assert "Виральный <b>хук</b> для NeuroMule" in body
    assert msg.TXT_INLINE_VIRAL_FOOTER in body
    assert article.input_message_content.parse_mode == "HTML"

    # ровно 1 ⚡ списан
    after = await load_user_billing(uid)
    assert after.energy_paid + after.energy_free == 9


@pytest.mark.asyncio
async def test_inline_ai_failure_triggers_refund(repo_module, monkeypatch) -> None:
    """OpenRouter упал → рефанд 1 ⚡ + AI-fail заглушка."""
    await init_billing_schema()
    uid = 700_005
    await repo_module.ensure_user(uid)
    await repo_module.set_user_tariff(uid, "ULTRA")
    import aiosqlite

    async with aiosqlite.connect(repo_module.DB_PATH) as db:
        await db.execute(
            "UPDATE users SET energy_paid=5, energy_free=0, energy=5, "
            "balance_energy=5, sub_crystals=0, buy_crystals=0, crystals=0 "
            "WHERE id=?",
            (uid,),
        )
        await db.commit()

    before = await load_user_billing(uid)

    refund_calls: list[str] = []
    real_refund = store.refund_charge

    async def _spy_refund(charge_id: str) -> bool:
        refund_calls.append(charge_id)
        return await real_refund(charge_id)

    monkeypatch.setattr(inline_flow, "refund_charge", _spy_refund)

    async def _explode(query_text: str) -> str:
        raise RuntimeError("boom from openrouter")

    monkeypatch.setattr(inline_flow, "_generate_inline_answer", _explode)

    q = _seed_query(uid, text="generate me a hit")
    await inline_query_handler(q)  # type: ignore[arg-type]

    article = q.answers[0]["results"][0]
    assert article.title == msg.TXT_INLINE_AI_FAILED_TITLE
    assert len(refund_calls) == 1

    after = await load_user_billing(uid)
    # 1 ⚡ списали и тут же вернули → баланс прежний
    assert before.energy_paid == after.energy_paid
