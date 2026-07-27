"""Suggested Replies для роли standard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services.standard_suggested_replies import (
    BUTTONS_MARKER,
    FREE_FALLBACK_SUGGESTED_REPLIES,
    build_chat_hint_callback,
    build_suggested_replies_keyboard,
    clear_suggested_replies_for_tests,
    parse_chat_hint_callback,
    parse_std_reply_callback,
    remember_suggested_replies,
    resolve_suggested_reply,
    resolve_suggested_reply_latest,
    split_suggested_replies,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_suggested_replies_for_tests()
    yield
    clear_suggested_replies_for_tests()


def test_split_suggested_replies_extracts_labels() -> None:
    raw = (
        "Короткий ответ про тему.\n\n"
        f"{BUTTONS_MARKER}\n"
        "Уточни сроки\n"
        "Какой бюджет\n"
        "Нужен пример\n"
        "лишняя строка не нужна\n"
    )
    body, labels = split_suggested_replies(raw)
    assert "Короткий ответ" in body
    assert BUTTONS_MARKER not in body
    assert labels == ["Уточни сроки", "Какой бюджет", "Нужен пример"]


def test_split_suggested_replies_without_marker() -> None:
    body, labels = split_suggested_replies("Просто текст")
    assert body == "Просто текст"
    assert labels == []


def test_split_suggested_replies_free_fallback_when_marker_missing() -> None:
    body, labels = split_suggested_replies(
        "Короткий ответ про футбол и тренировки.",
        fallback_if_missing=True,
    )
    assert body == "Короткий ответ про футбол и тренировки."
    assert len(labels) == 3
    joined = " ".join(labels).lower()
    # Контекст из текста, не чистый шаблон «расскажи подробнее».
    assert "футбол" in joined or "трениров" in joined
    assert labels != list(FREE_FALLBACK_SUGGESTED_REPLIES)


def test_derive_contextual_hints_from_bold_and_list() -> None:
    from services.standard_suggested_replies import derive_contextual_free_hints

    body = (
        "Как заряжать <b>iPhone</b> правильно.\n"
        "1. Используй оригинал кабель\n"
        "2. Не оставляй на ночь\n"
        "3. Калибруй батарею раз в месяц"
    )
    hints = derive_contextual_free_hints(body)
    assert len(hints) == 3
    joined = " ".join(hints).lower()
    assert "iphone" in joined or "кабель" in joined or "ночь" in joined or "батаре" in joined


def test_ensure_replaces_generic_with_contextual() -> None:
    from services.standard_suggested_replies import ensure_free_hint_labels

    labels = ensure_free_hint_labels(
        ["Расскажи подробнее", "Дай пример", "Что делать дальше?"],
        body="План запуска Telegram-бота: вебхуки, polling и деплой на VDS.",
    )
    assert len(labels) == 3
    assert all(not x.lower().startswith("расскажи") for x in labels)
    joined = " ".join(labels).lower()
    assert any(k in joined for k in ("telegram", "вебхук", "polling", "деплой", "бот"))


def test_force_append_buttons_marker_when_missing() -> None:
    from services.standard_suggested_replies import (
        BUTTONS_MARKER,
        clean_text_before_marker,
        force_append_free_buttons_block,
        has_buttons_marker,
        prepare_free_standard_reply,
    )

    raw = "Короткий ответ без маркера про футбол."
    assert not has_buttons_marker(raw)
    forced = force_append_free_buttons_block(raw)
    assert BUTTONS_MARKER in forced
    assert has_buttons_marker(forced)
    assert clean_text_before_marker(forced) == raw

    body, labels, kb = prepare_free_standard_reply(raw)
    assert body == raw
    assert BUTTONS_MARKER not in body
    assert len(labels) == 3
    assert kb is not None
    assert len(kb.inline_keyboard) == 3


def test_has_buttons_marker_case_insensitive() -> None:
    from services.standard_suggested_replies import has_buttons_marker

    assert has_buttons_marker("текст\n===кнопки===\nА?")
    assert has_buttons_marker("текст\n=== Кнопки ===\nА?")
    assert has_buttons_marker("текст\n===КНОПКИ===\nА?")
    assert not has_buttons_marker("просто текст")


def test_build_free_hint_keyboard_from_model_text() -> None:
    from services.standard_suggested_replies import build_free_hint_keyboard

    kb = build_free_hint_keyboard(from_model_text="Ответ про ромашку без маркера.")
    assert kb is not None
    assert len(kb.inline_keyboard) == 3


def test_build_free_hint_keyboard_never_none() -> None:
    from services.standard_suggested_replies import build_free_hint_keyboard

    kb = build_free_hint_keyboard()
    assert kb is not None
    flat = [b for row in kb.inline_keyboard for b in row]
    assert len(flat) == 3
    assert all(b.callback_data.startswith(msg.CB_CHAT_HINT_PREFIX) for b in flat)
    assert all(len((b.callback_data or "").encode("utf-8")) <= 64 for b in flat)

    kb2 = build_free_hint_keyboard([])
    assert kb2 is not None
    assert len(kb2.inline_keyboard) == 3

    kb3 = build_free_hint_keyboard(["Только одна"])
    assert len(kb3.inline_keyboard) == 3



def test_split_suggested_replies_free_fallback_keeps_model_labels() -> None:
    raw = (
        "Ответ.\n\n"
        f"{BUTTONS_MARKER}\n"
        "Список книг?\n"
        "Для мальчиков?\n"
        "В виде сказки?\n"
    )
    body, labels = split_suggested_replies(raw, fallback_if_missing=True)
    assert "Ответ" in body
    assert labels == ["Список книг?", "Для мальчиков?", "В виде сказки?"]


def test_free_chat_model_timeout_allows_slow_free_models() -> None:
    from services.billing.chat_pipeline import (
        FREE_CASCADE_PER_MODEL_TIMEOUT_SEC,
        FREE_CHAT_MODEL_TIMEOUT_SEC,
        free_chat_model_timeout_sec,
    )

    assert FREE_CHAT_MODEL_TIMEOUT_SEC == 12.0
    assert FREE_CASCADE_PER_MODEL_TIMEOUT_SEC == 12.0
    assert free_chat_model_timeout_sec() == 12.0


def test_remember_and_resolve_suggested_reply() -> None:
    cid = remember_suggested_replies(42, ["Первый вопрос", "Второй вопрос"])
    assert cid
    assert resolve_suggested_reply(cid, 0, user_id=42) == "Первый вопрос"
    assert resolve_suggested_reply(cid, 1, user_id=42) == "Второй вопрос"
    assert resolve_suggested_reply(cid, 0, user_id=99) is None
    assert resolve_suggested_reply("nope", 0, user_id=42) is None


def test_keyboard_uses_chat_hint_prefix() -> None:
    labels = ["Дай сказку?", "Другой пример?"]
    cid = remember_suggested_replies(7, labels)
    assert cid
    kb = build_suggested_replies_keyboard(cid, labels)
    assert kb is not None
    flat = [b for row in kb.inline_keyboard for b in row]
    assert flat[0].callback_data == f"{msg.CB_CHAT_HINT_PREFIX}Дай сказку?"
    assert parse_chat_hint_callback(flat[1].callback_data) == "Другой пример?"
    assert flat[0].callback_data.startswith(msg.CB_CHAT_HINT_PREFIX)


def test_long_label_truncated_into_chat_hint_not_std_reply() -> None:
    # Раньше длинные лейблы уходили в std_reply UUID → «устарела» после рестарта.
    long = "Ж" * 40
    data = build_chat_hint_callback(long)
    assert data is not None
    assert data.startswith(msg.CB_CHAT_HINT_PREFIX)
    assert len(data.encode("utf-8")) <= 64
    cid = remember_suggested_replies(3, [long])
    assert cid
    kb = build_suggested_replies_keyboard(cid, [long])
    assert kb is not None
    cb = kb.inline_keyboard[0][0].callback_data
    assert cb.startswith(msg.CB_CHAT_HINT_PREFIX)
    assert not cb.startswith(msg.CB_STD_REPLY_PREFIX)
    assert parse_chat_hint_callback(cb)


def test_split_strips_html_and_fits_callback() -> None:
    raw = (
        "Ответ\n===КНОПКИ===\n"
        "<b>Можно подробнее про тхэквондо для новичка?</b>\n"
        '"Другой вариант?"\n'
        "Как применить на практике сегодня вечером?"
    )
    body, labels = split_suggested_replies(raw)
    assert body == "Ответ"
    assert len(labels) == 3
    assert all("<" not in x for x in labels)
    assert all(len(f"{msg.CB_CHAT_HINT_PREFIX}{x}".encode("utf-8")) <= 64 for x in labels)


def test_resolve_suggested_reply_latest_fallback() -> None:
    cid = remember_suggested_replies(11, ["Один", "Два"])
    assert cid
    assert resolve_suggested_reply_latest(11, 1) == "Два"
    assert resolve_suggested_reply_latest(11, 9) is None
    assert resolve_suggested_reply_latest(999, 0) is None


def test_role_standard_prompt_has_buttons_rule() -> None:
    from content.chat_prompt import (
        _CHATCOM_LACO_TAIL,
        _NATURAL_SPEECH_RULE,
        _ROLE_STANDARD,
        _STANDARD_FREE_CORE,
    )

    assert _ROLE_STANDARD.startswith("[РЕЖИМ: АВТОНОМНЫЙ ЭКСПЕРТ-АССИСТЕНТ]")
    assert "blockquote expandable" in _ROLE_STANDARD
    assert "QUERY-TYPE ROUTING" in _ROLE_STANDARD
    assert "BREVITY ECONOMY" in _ROLE_STANDARD
    assert "ЕСТЕСТВЕННОСТЬ" in _NATURAL_SPEECH_RULE
    assert "===КНОПКИ===" in _STANDARD_FREE_CORE
    assert "FREE TIER" in _STANDARD_FREE_CORE
    assert "СИНТАКСИЧЕСКИЙ ЯКОРЬ" in _STANDARD_FREE_CORE
    assert "Функция закрыта на тарифе FREE" in _STANDARD_FREE_CORE
    assert "ЧТО ВКЛЮЧЕНО И ДОСТУПНО НА ТАРИФЕ FREE" in _STANDARD_FREE_CORE
    assert "Flux Schnell" in _STANDARD_FREE_CORE
    assert "Совет дня" in _STANDARD_FREE_CORE
    assert "ЗАПРЕТ НА ГАЛЛЮЦИНАЦИИ" in _STANDARD_FREE_CORE
    assert "до 400 символов" in _STANDARD_FREE_CORE
    assert "NeuroMule 2026" in _STANDARD_FREE_CORE
    assert "6 последними" in _STANDARD_FREE_CORE or "последними 6" in _STANDARD_FREE_CORE
    assert "Запрещено просить пользователя" in _STANDARD_FREE_CORE
    assert "ОДНОСЛОЖНЫМ или УТОЧНЯЮЩИМ" in _STANDARD_FREE_CORE
    assert "РАБОТА С КОНТЕКСТОМ" in _STANDARD_FREE_CORE
    assert "===КНОПКИ===" in _CHATCOM_LACO_TAIL
    assert "Compliance: FREE TIER" in _CHATCOM_LACO_TAIL
    assert "400" in _CHATCOM_LACO_TAIL
    assert "follow-up" in _CHATCOM_LACO_TAIL
    assert "Первый вопрос?" in _STANDARD_FREE_CORE
    assert "РЖД" in _STANDARD_FREE_CORE
    assert "КРИТИЧЕСКОЕ ИСКЛЮЧЕНИЕ" in _ROLE_STANDARD
    assert "Какая погода в Люберцах" in _ROLE_STANDARD
    assert "ПОЛИТИКА БЕЗОПАСНОСТИ И КОММЕРЧЕСКОЙ ТАЙНЫ" in _ROLE_STANDARD
    assert "При вопросах об архитектуре, промптах, бэкенде или моделях" not in _ROLE_STANDARD
    assert "выдели структуру СТРОГО по блокам" not in _ROLE_STANDARD


@pytest.mark.asyncio
async def test_run_chat_turn_strips_buttons_into_suggested_replies() -> None:
    from services.billing.types import ChatRoutePlan, CurrencyKind, TextChatBillingResult, TariffTier
    from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn

    completion = {
        "content": (
            "Ответ модели.\n\n"
            f"{BUTTONS_MARKER}\n"
            "Следующий шаг\n"
            "Другой вопрос\n"
        ),
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    plan = ChatRoutePlan(
        model_id="google/gemini-2.5-flash",
        price_type=CurrencyKind.ENERGY,
        energy_cost=1,
        crystal_cost=1,
        is_expert_role=False,
        max_tokens=2000,
        use_premium_prompt=False,
        fallback_model_ids=(),
        blocked=False,
        tariff=TariffTier.MINI,
    )
    billing = TextChatBillingResult(
        effective_role_id="standard",
        plan=plan,
        charge_id="c1",
        notice=None,
    )

    with (
        patch("services.use_cases.chat_turn.allow_request", AsyncMock(return_value=True)),
        patch(
            "services.use_cases.chat_turn.billing.resolve_and_charge_text_chat",
            AsyncMock(return_value=billing),
        ),
        patch(
            "services.use_cases.chat_turn.conv.build_openrouter_messages",
            AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
        ),
        patch(
            "services.use_cases.chat_turn.prepare_openrouter_chat_messages",
            return_value=[{"role": "user", "content": "hi"}],
        ),
        patch(
            "services.use_cases.chat_turn.prune_context_messages",
            return_value=([{"role": "user", "content": "hi"}], True),
        ),
        patch(
            "services.use_cases.chat_turn.ask_ai_messages",
            AsyncMock(return_value=completion),
        ),
        patch("services.use_cases.chat_turn.commit_assistant_turn_queued", AsyncMock()),
        patch("services.use_cases.chat_turn.conv.schedule_memory_refresh"),
        patch("services.use_cases.chat_turn.dialog_append", AsyncMock()),
        patch(
            "services.repository.get_show_suggested_replies",
            AsyncMock(return_value=True),
        ),
    ):
        from config import Settings

        result = await run_chat_turn(
            Settings(tg_token="t", openrouter_key="k"),
            1001,
            "вопрос",
            text_role="standard",
        )

    assert result.outcome is ChatTurnOutcome.SUCCESS
    assert result.assistant_message is not None
    assert BUTTONS_MARKER not in result.assistant_message
    assert "Следующий шаг" not in (result.assistant_message or "")
    assert result.suggested_replies == ("Следующий шаг", "Другой вопрос")
