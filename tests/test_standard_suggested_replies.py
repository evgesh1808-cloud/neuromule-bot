"""Suggested Replies для роли standard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from content import messages as msg
from services.standard_suggested_replies import (
    BUTTONS_MARKER,
    FREE_FALLBACK_SUGGESTED_REPLIES,
    bind_hint_session_message,
    build_chat_hint_callback,
    build_hint_keyboard,
    build_suggested_replies_keyboard,
    clear_suggested_replies_for_tests,
    create_hint_session,
    get_hint_session,
    parse_chat_hint_callback,
    parse_hint_btn_callback,
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
    assert labels == ["Уточни сроки", "Какой бюджет?", "Нужен пример"]


def test_paid_compliance_tail_strips_ban_when_hints_requested() -> None:
    from content.chat_prompt import build_user_compliance_tail

    off = build_user_compliance_tail(
        premium=True,
        text_role="standard",
        request_suggested_replies=False,
        user_text="почему небо голубое",
    )
    on = build_user_compliance_tail(
        premium=True,
        text_role="standard",
        request_suggested_replies=True,
        user_text="почему небо голубое",
    )
    assert "Без блоков ===КНОПКИ===" in off
    assert "Без блоков ===КНОПКИ===" not in on
    assert "===КНОПКИ===" in on
    assert "[Системный хвост подсказок:" in on


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


def test_copy_pack_body_yields_no_contextual_hints() -> None:
    from services.standard_suggested_replies import (
        derive_contextual_free_hints,
        ensure_free_hint_labels,
        split_suggested_replies,
    )

    body = (
        "Готово! Разные варианты на выбор (нажмите на текст, чтобы скопировать):\n\n"
        "🎉 <b>Трогательное и душевное</b>\n"
        "<pre>\nС днём рождения, мама!\n</pre>\n\n"
        "🥂 <b>Короткое СМС-поздравление</b>\n"
        "<pre>\nС ДР!\n</pre>\n\n"
        "🚀 <b>Драйвовое</b>\n"
        "<pre>\nУра, праздник!\n</pre>\n\n"
        "💼 <b>Официальное</b>\n"
        "<pre>\nПоздравляю с днём рождения.\n</pre>"
    )
    assert derive_contextual_free_hints(body) == []
    # fallback не должен тащить «Трогательное…» из заголовков стилей.
    _clean, labels = split_suggested_replies(body, fallback_if_missing=True)
    joined = " ".join(labels).lower()
    assert "трогательн" not in joined
    assert "смс-поздравление" not in joined
    # Если только FREE static — тоже ок, но не copy-pack titles.
    padded = ensure_free_hint_labels(body=body)
    joined2 = " ".join(padded).lower()
    assert "трогательн" not in joined2


def test_polish_hint_label_capitalizes_and_adds_question_mark() -> None:
    from services.standard_suggested_replies import polish_hint_label

    assert polish_hint_label("какие риски у вебхуков") == "Какие риски у вебхуков?"
    assert polish_hint_label("  как применить polling  ") == "Как применить polling?"
    assert polish_hint_label("Уточни сроки") == "Уточни сроки"


def test_assistant_text_from_callback_message() -> None:
    from platforms.handlers.generation_fsm import _assistant_text_from_callback_message

    class _Msg:
        html_text = "<b>Старый ответ</b> про танцы"
        text = "plain"
        caption = None

    assert "Старый ответ" in _assistant_text_from_callback_message(_Msg())


def test_expand_suggested_reply_makes_distinct_practical_asks() -> None:
    from services.standard_suggested_replies import expand_suggested_reply_prompt

    a = expand_suggested_reply_prompt("Про искренний интерес?")
    b = expand_suggested_reply_prompt("Ещё про игровая форма?")
    c = expand_suggested_reply_prompt("Что учесть в мотивация?")
    assert a != b != c
    assert "искренний интерес" in a.lower()
    assert "игровая форма" in b.lower()
    assert "мотивация" in c.lower()
    assert "только этот пункт" not in a.lower()
    assert "опираясь" not in a.lower()
    assert "SYSTEM SECURITY" not in a


def test_free_hints_fit_chat_hint_callback() -> None:
    from services.standard_suggested_replies import (
        build_chat_hint_callback,
        derive_contextual_free_hints,
        parse_chat_hint_callback,
    )

    body = (
        "1. Искренний интерес: узнайте стили\n"
        "2. Игровая форма: танцуйте как игра\n"
        "3. Поддержка, а не критика: хвалите усилия"
    )
    hints = derive_contextual_free_hints(body)
    assert len(hints) == 3
    joined = " ".join(hints).lower()
    assert "как:" not in joined
    assert "пример:" not in joined
    assert any(h.lower().startswith("про ") for h in hints)
    for h in hints:
        data = build_chat_hint_callback(h)
        assert data is not None
        assert len(data.encode("utf-8")) <= 64
        sent = parse_chat_hint_callback(data)
        assert sent == h
        assert "…" not in h
        assert "искрений" not in h.lower()


def test_anchor_drops_trailing_preposition() -> None:
    from services.standard_suggested_replies import _clip_anchor

    assert _clip_anchor("наблюдать за") == "наблюдать"
    assert _clip_anchor("начать с") == "начать"


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
    assert all(h[0].isupper() for h in hints if h and h[0].isalpha())
    assert any("?" in h for h in hints)


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


def test_keyboard_uses_std_reply_with_short_display() -> None:
    labels = ["Дай сказку про дракона и рыцаря?", "Другой пример?"]
    cid = remember_suggested_replies(7, labels)
    assert cid
    kb = build_suggested_replies_keyboard(cid, labels)
    assert kb is not None
    flat = [b for row in kb.inline_keyboard for b in row]
    assert flat[0].callback_data == f"{msg.CB_STD_REPLY_PREFIX}0:{cid}"
    assert flat[1].callback_data == f"{msg.CB_STD_REPLY_PREFIX}1:{cid}"
    assert resolve_suggested_reply(cid, 0, user_id=7) == "Дай сказку про дракона и рыцаря?"
    assert len(flat[0].text) <= 34
    assert flat[0].text.endswith("…") or len(labels[0]) <= 34


def test_long_label_stored_full_via_std_reply() -> None:
    # Полный смысл в кэше; на кнопке — короткий display; callback без chat_hint-обрезки.
    long = "Как применить тхэквондо для новичка?"
    assert len(long) <= 48
    cid = remember_suggested_replies(3, [long])
    assert cid
    kb = build_suggested_replies_keyboard(cid, [long])
    assert kb is not None
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data.startswith(msg.CB_STD_REPLY_PREFIX)
    assert not btn.callback_data.startswith(msg.CB_CHAT_HINT_PREFIX)
    assert resolve_suggested_reply(cid, 0, user_id=3) == long
    assert len(btn.text) <= 34
    from services.standard_suggested_replies import expand_suggested_reply_prompt

    prompt = expand_suggested_reply_prompt(long)
    assert "тхэквондо" in prompt.lower()
    assert "SYSTEM SECURITY" not in prompt
    assert "опираясь на" not in prompt.lower()


def test_chat_hint_still_fits_64_bytes() -> None:
    long = "Ж" * 40
    data = build_chat_hint_callback(long)
    assert data is not None
    assert data.startswith(msg.CB_CHAT_HINT_PREFIX)
    assert len(data.encode("utf-8")) <= 64
    assert parse_chat_hint_callback(data)


def test_split_strips_html_and_keeps_readable_labels() -> None:
    raw = (
        "Ответ\n===КНОПКИ===\n"
        "<b>Можно подробнее про тхэквондо?</b>\n"
        '"Другой вариант?"\n'
        "Как применить?"
    )
    body, labels = split_suggested_replies(raw)
    assert body == "Ответ"
    assert len(labels) == 3
    assert all("<" not in x for x in labels)
    assert "тхэквондо" in labels[0].lower()


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
        _NEUROMULE_SECURITY_POLICY,
        _NO_REALTIME_HALLUCINATION_RULE,
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
    assert "коротких вопроса" in _CHATCOM_LACO_TAIL
    assert "Про возраст?" in _STANDARD_FREE_CORE
    assert "Ещё про" in _STANDARD_FREE_CORE
    assert "Не копируй" in _STANDARD_FREE_CORE or "копировать" in _STANDARD_FREE_CORE
    assert "РЖД" in _STANDARD_FREE_CORE
    assert "КРИТИЧЕСКОЕ ИСКЛЮЧЕНИЕ" in _ROLE_STANDARD
    assert "Кто такой Пушкин" in _ROLE_STANDARD
    assert "ЗАПРЕЩЕНО выдумывать" in _NEUROMULE_SECURITY_POLICY
    assert "Яндекс.Погода" in _NEUROMULE_SECURITY_POLICY
    assert "ЗАПРЕТ НА ГАЛЛЮЦИНАЦИИ" in _NO_REALTIME_HALLUCINATION_RULE
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
    # Pref ON → fallback может дописать 3-й лейбл; первые два — из ответа модели.
    assert result.suggested_replies[:2] == ("Следующий шаг", "Другой вопрос")
    assert len(result.suggested_replies) >= 2


@pytest.mark.asyncio
async def test_run_chat_turn_hint_click_uses_temperature_055() -> None:
    """Клик по кнопке (есть anchor) → temperature=0.55, без клонов ответов."""
    from services.billing.types import ChatRoutePlan, CurrencyKind, TextChatBillingResult, TariffTier
    from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn

    ask = AsyncMock(
        return_value={
            "content": "Новый практический ответ.",
            "prompt_tokens": 8,
            "completion_tokens": 4,
        }
    )
    plan = ChatRoutePlan(
        model_id="google/gemini-2.5-flash",
        price_type=CurrencyKind.ENERGY,
        energy_cost=0,
        crystal_cost=0,
        is_expert_role=False,
        max_tokens=2000,
        use_premium_prompt=False,
        fallback_model_ids=(),
        blocked=False,
        tariff=TariffTier.FREE,
        temperature=None,
    )
    billing = TextChatBillingResult(
        effective_role_id="standard",
        plan=plan,
        charge_id=None,
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
            AsyncMock(
                return_value=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "По теме «танцы»: Как на практике?"},
                ]
            ),
        ),
        patch(
            "services.use_cases.chat_turn.prepare_openrouter_chat_messages",
            side_effect=lambda msgs, **kw: msgs,
        ),
        patch(
            "services.use_cases.chat_turn.prune_context_messages",
            side_effect=lambda msgs, **kw: (msgs, True),
        ),
        patch("services.use_cases.chat_turn.ask_ai_messages", ask),
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
            1002,
            "По теме «танцы»: Как на практике?",
            text_role="standard",
            anchor_assistant_text="2. Игровая форма: танцуйте дома.",
        )

    assert result.outcome is ChatTurnOutcome.SUCCESS
    assert ask.await_count >= 1
    temps = [
        c.kwargs.get("temperature")
        for c in ask.await_args_list
        if "temperature" in c.kwargs
    ]
    assert 0.55 in temps


def test_hint_keyboard_callback_data_within_telegram_64() -> None:
    """Telegram: callback_data ≤ 64 байт UTF-8 (для ASCII btn: — и символов тоже)."""
    labels = [
        "Как применить тхэквондо для новичка?",
        "Дай пример разминки перед спаррингом?",
        "Какие ошибки типичны на первой тренировке?",
    ]
    action_uuid = create_hint_session(
        55,
        body="Ответ про тхэквондо.",
        labels=labels,
        root_user_prompt="Как начать тхэквондо?",
    )
    kb = build_hint_keyboard(action_uuid, labels)
    assert kb is not None
    for row in kb.inline_keyboard:
        for btn in row:
            data = btn.callback_data or ""
            assert len(data) <= 64
            assert len(data.encode("utf-8")) <= 64
            assert data.startswith(msg.CB_HINT_BTN_PREFIX)
            parsed = parse_hint_btn_callback(data)
            assert parsed is not None
            idx, uid = parsed
            assert uid == action_uuid
            assert 0 <= idx < 3


def test_hint_session_isolated_from_legacy_cache() -> None:
    """Legacy ``_CACHE`` / remember_* не должны пересекаться с HintSession."""
    labels_legacy = ["Старая кнопка A", "Старая кнопка B"]
    labels_hint = ["Новая кнопка X", "Новая кнопка Y"]

    cid = remember_suggested_replies(42, labels_legacy)
    assert cid
    assert resolve_suggested_reply(cid, 0, user_id=42) == "Старая кнопка A"
    assert resolve_suggested_reply_latest(42, 1) == "Старая кнопка B"

    action_uuid = create_hint_session(
        42,
        body="Тело ответа бота.",
        labels=labels_hint,
        root_user_prompt="Корневой вопрос пользователя",
        message_id=None,
    )
    bind_hint_session_message(action_uuid, 9001)
    session = get_hint_session(action_uuid, user_id=42)
    assert session is not None
    assert session.message_id == 9001
    assert session.body == "Тело ответа бота."
    assert session.root_user_prompt == "Корневой вопрос пользователя"
    assert "Новая кнопка X" in session.labels

    # HintSession не затёр legacy: старые кнопки всё ещё резолвятся.
    assert resolve_suggested_reply(cid, 0, user_id=42) == "Старая кнопка A"
    assert resolve_suggested_reply_latest(42, 0) == "Старая кнопка A"

    # Legacy remember не затирает HintSession (даже если сносит предыдущий context_id).
    cid2 = remember_suggested_replies(42, ["Ещё legacy"])
    assert cid2
    assert get_hint_session(action_uuid, user_id=42) is not None
    assert resolve_suggested_reply(cid, 0, user_id=42) is None  # prev legacy dropped
    assert resolve_suggested_reply(cid2, 0, user_id=42) == "Ещё legacy"

    # Чужой user_id не читает чужую HintSession.
    assert get_hint_session(action_uuid, user_id=99) is None
