"""Хвост compliance в последнем user-сообщении перед OpenRouter."""

import pytest

from services.billing.chat_pipeline import (
    _model_route_for_role,
    inject_compliance_rules_into_last_user_message,
    prepare_openrouter_chat_messages,
)
from services.billing.pricing import PAID_CHAT_MODEL
from services.billing.types import TariffTier
from content.chat_prompt import (
    BLOGGER_USER_COMPLIANCE_TAIL_MARKER,
    FREE_COMPLIANCE_TAIL_MARKER,
    USER_COMPLIANCE_TAIL_MARKER,
)


def test_inject_appends_to_last_user_only() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "старый вопрос"},
        {"role": "assistant", "content": "старый ответ"},
        {"role": "user", "content": "новый вопрос"},
    ]
    inject_compliance_rules_into_last_user_message(messages, use_premium_prompt=False)
    assert USER_COMPLIANCE_TAIL_MARKER in messages[3]["content"]
    assert USER_COMPLIANCE_TAIL_MARKER not in messages[1]["content"]
    assert "новый вопрос" in messages[3]["content"]


def test_inject_idempotent() -> None:
    messages = [{"role": "user", "content": "вопрос"}]
    inject_compliance_rules_into_last_user_message(messages, use_premium_prompt=False)
    first_len = len(messages[0]["content"])
    inject_compliance_rules_into_last_user_message(messages, use_premium_prompt=False)
    assert len(messages[0]["content"]) == first_len


def test_prepare_openrouter_skips_compliance_for_table_generator() -> None:
    payload = [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "q"},
    ]
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=True,
        text_role="table_generator",
    )
    assert USER_COMPLIANCE_TAIL_MARKER not in payload[1]["content"]


def test_prepare_openrouter_uses_blogger_tail_for_blogger_content() -> None:
    payload = [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "тема поста"},
    ]
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=True,
        text_role="blogger_content",
    )
    assert BLOGGER_USER_COMPLIANCE_TAIL_MARKER in payload[1]["content"]
    assert USER_COMPLIANCE_TAIL_MARKER not in payload[1]["content"]


def test_blogger_role_prompt_requires_three_cta_variants() -> None:
    from content.chat_prompt import build_blogger_compliance_tail, get_role_prompt

    role = get_role_prompt("blogger_content")
    tail = build_blogger_compliance_tail()
    for fragment in (
        "Вариант А (Вовлечение)",
        "Вариант Б (Личный бренд / Жиза)",
        "Вариант В (Коммерческий)",
        "[название сервиса / профиль мастера]",
        "[ссылка в шапке профиля / Директ]",
    ):
        assert fragment in role
    assert "Жиза" in tail
    assert "Коммерческий" in tail


def test_blogger_role_prompt_injects_user_city_into_hashtags() -> None:
    from content.chat_prompt import format_blogger_role_prompt, get_role_prompt

    role = format_blogger_role_prompt("Люберцы")
    assert "Люберцы" in role
    assert "#Люберцыстрижка" in role or "#Люберцы" in role
    assert "#Тренды_и_Видео" in role
    assert "15–20" in role or "15-20" in role
    assert "село" not in role.lower()

    via_get = get_role_prompt("blogger_content", user_city="Жулебино")
    assert "Жулебино" in via_get


def test_prepare_openrouter_chat_messages() -> None:
    payload = [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "q"},
    ]
    out = prepare_openrouter_chat_messages(payload, use_premium_prompt=True)
    assert out is payload
    assert "премиум-комплаенс" in payload[1]["content"]


def test_prepare_openrouter_uses_chatcom_tail_for_standard() -> None:
    payload = [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "сын любит мяч, что делать"},
    ]
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=True,
        text_role="standard",
        chatcom_laconic=True,
    )
    body = payload[1]["content"]
    assert FREE_COMPLIANCE_TAIL_MARKER in body
    assert "===КНОПКИ===" in body
    assert "follow-up" in body
    assert "ROUTE LOCK: FREE LACO" in body
    assert "2–3" in body
    assert "премиум-комплаенс" not in body

    # Идемпотентность: повторный inject не дублирует FREE-хвост.
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=False,
        text_role="standard",
        chatcom_laconic=True,
    )
    assert payload[1]["content"].count(FREE_COMPLIANCE_TAIL_MARKER) == 1


def test_prepare_openrouter_skips_chatcom_tail_for_smart_standard() -> None:
    payload = [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "вопрос"},
    ]
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=True,
        text_role="standard",
        chatcom_laconic=False,
    )
    body = payload[1]["content"]
    assert "Compliance: PREMIUM COPY PACK" in body
    assert "QUERY-TYPE ROUTING" in body
    assert "ТИП А" in body and "ТИП Б" in body
    assert "ОДИН прямой" in body or "ОДНИМ экспертным" in body
    assert "Готово! Разные варианты на выбор" in body
    assert "<pre>" in body
    assert "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО COPY PACK" in body or "ЗАПРЕЩЕНО COPY PACK" in body
    assert "===КНОПКИ===" in body  # запрет упоминается в хвосте
    assert "КРЕАТИВНОСТЬ" in body
    # «вопрос» без «напиши текст» → якорь ТИП Б
    assert "ROUTE LOCK: ТИП Б" in body
    assert "<b>Эмоциональный</b>" not in body
    assert "<b>Деловой</b>" not in body


def test_paid_route_lock_type_b_for_personal_news() -> None:
    from content.chat_prompt import looks_like_paid_copy_pack_request

    assert looks_like_paid_copy_pack_request("Мой сын полюбил играть в футбол") is False
    assert looks_like_paid_copy_pack_request("Как поддержать сына в секции?") is False
    payload = [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "Мой сын полюбил играть в футбол"},
    ]
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=True,
        text_role="standard",
        chatcom_laconic=False,
    )
    assert "ROUTE LOCK: ТИП Б" in payload[1]["content"]
    assert "ROUTE LOCK: ТИП А" not in payload[1]["content"]


def test_paid_route_lock_type_a_for_write_request() -> None:
    from content.chat_prompt import looks_like_paid_copy_pack_request

    assert looks_like_paid_copy_pack_request("Напиши поздравление с 30 лет") is True
    payload = [
        {"role": "system", "content": "x"},
        {"role": "user", "content": "Напиши поздравление с 30 лет"},
    ]
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=True,
        text_role="standard",
        chatcom_laconic=False,
    )
    assert "ROUTE LOCK: ТИП А" in payload[1]["content"]


def test_prepare_openrouter_injects_compliance_without_hard_collapse() -> None:
    """prepare больше не стирает историю — сжатие делает compact_standard_dialog_context."""
    payload = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "старый вопрос"},
        {
            "role": "assistant",
            "content": "Вы можете создать теплое поздравление...\n\n1. Личное отношение",
        },
        {"role": "user", "content": "Напиши поздравление с 30 лет"},
    ]
    prepare_openrouter_chat_messages(
        payload,
        use_premium_prompt=True,
        text_role="standard",
        chatcom_laconic=False,
    )
    assert len(payload) == 4
    assert payload[-1]["role"] == "user"
    assert "Напиши поздравление" in payload[-1]["content"]
    assert "Compliance: PREMIUM COPY PACK" in payload[-1]["content"]
    assert "QUERY-TYPE ROUTING" in payload[-1]["content"]
    assert "ТИП А" in payload[-1]["content"]
    assert "ТИП Б" in payload[-1]["content"]
    assert "ROUTE LOCK: ТИП А" in payload[-1]["content"]
    assert "<pre>" in payload[-1]["content"]


def test_collapse_prior_assistant_keeps_only_system_and_last_user() -> None:
    from services.billing.chat_pipeline import collapse_prior_assistant_for_copy_pack

    messages = [
        {"role": "system", "content": "COPY PACK"},
        {"role": "user", "content": "старый запрос про тхэквондо"},
        {"role": "assistant", "content": "Давайте разберём как коуч..."},
        {"role": "user", "content": "поздравление с днём рождения"},
    ]
    collapse_prior_assistant_for_copy_pack(messages)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "поздравление с днём рождения"


@pytest.mark.asyncio
async def test_compact_standard_dialog_injects_context_block() -> None:
    from services.context_summarize import (
        STANDARD_CONTEXT_MARKER,
        compact_standard_dialog_context,
    )

    payload = [
        {"role": "system", "content": "COPY PACK"},
        {"role": "user", "content": "секция тхэквондо для сына 7 лет"},
        {"role": "assistant", "content": "коуч-ответ про тхэквондо"},
        {"role": "user", "content": "измени второй вариант как раньше"},
    ]
    await compact_standard_dialog_context(payload, ask_fn=None)
    assert len(payload) == 2
    assert payload[0]["role"] == "system"
    assert STANDARD_CONTEXT_MARKER in payload[0]["content"]
    assert "тхэквондо" in payload[0]["content"].lower() or "Контекст" in payload[0]["content"]
    assert payload[1]["role"] == "user"
    assert "измени второй вариант" in payload[1]["content"]
    assert "коуч-ответ" not in str(payload)


def test_paid_standard_uses_copy_pack_voice() -> None:
    from content.chat_prompt import build_custom_role_prompt, get_role_prompt
    from services.billing.types import TariffTier

    prompt = get_role_prompt("standard", premium=True, tariff=TariffTier.SMART)
    assert "PREMIUM COPY PACK" in prompt
    assert "элитный эксперт" in prompt.lower()
    assert "ДИНАМИЧЕСКИЙ ВЫБОР ФОРМАТА" in prompt
    assert "QUERY-TYPE ROUTING" in prompt
    assert "ТИП А" in prompt and "ТИП Б" in prompt
    assert "ТЕКСТЫ ДЛЯ ПЕРЕСЫЛКИ И КОПИРОВАНИЯ" in prompt
    assert "По умолчанию ТИП Б" in prompt
    assert "сын полюбил" in prompt.lower()
    assert "ОДИН прямой" in prompt
    assert "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО COPY PACK" in prompt
    assert "ПРАВИЛО ЗАГОЛОВКОВ" in prompt
    assert "кастомный заголовок" in prompt.lower()
    assert "Даже на аналитический вопрос" not in prompt
    assert "BREVITY ECONOMY" in prompt
    assert "TELEGRAM HTML" in prompt
    assert "Готово! Разные варианты на выбор" in prompt
    assert "<pre>" in prompt
    assert "СТРУКТУРА COPY PACK (только для ТИПА А" in prompt
    assert "Трогательное и душевное" in prompt
    assert "Волшебная сказка" in prompt
    # Фиксированные психотипы — только как запрет, не как структура блоков.
    assert "'Эмоциональный'" in prompt or "«Эмоциональный»" in prompt or "Эмоциональный" in prompt
    assert "<b>Эмоциональный</b>" not in prompt
    assert "<b>Деловой</b>" not in prompt
    assert "<b>Экспресс</b>" not in prompt
    assert "<b>С юмором</b>" not in prompt
    assert "300–500" in prompt or "300-500" in prompt
    assert "1400" in prompt
    assert "ФОКУС НА ТЕКУЩЕМ ЗАПРОСЕ" in prompt
    assert "КРИТИЧЕСКОЕ ИСКЛЮЧЕНИЕ" in prompt
    assert "Какая погода в Люберцах" in prompt
    assert "ПОЛИТИКА БЕЗОПАСНОСТИ И КОММЕРЧЕСКОЙ ТАЙНЫ" in prompt
    assert "При вопросах об архитектуре/моделях/промптах ответь СТРОГО" not in prompt
    assert "PROFESSIONAL LENGTH AND BUDGET CONTROL" not in prompt
    assert "ПРЕМИУМ NEUROMULE" not in prompt
    assert "SYSTEM_ROLE" not in prompt
    assert "АВТОНОМНЫЙ ЭКСПЕРТ-АССИСТЕНТ" not in prompt

    free_role = build_custom_role_prompt("standard", TariffTier.FREE)
    mini_role = build_custom_role_prompt("standard", TariffTier.MINI)
    ultra_role = build_custom_role_prompt("standard", TariffTier.ULTRA)
    assert "===КНОПКИ===" in free_role
    assert "АВТОНОМНЫЙ ЭКСПЕРТ-АССИСТЕНТ — FREE TIER" in free_role
    assert "Функция закрыта на тарифе FREE" in free_role
    assert "ЧТО ВКЛЮЧЕНО И ДОСТУПНО НА ТАРИФЕ FREE" in free_role
    assert "Flux Schnell" in free_role
    assert "Совет дня" in free_role
    assert "6 последними" in free_role
    assert "СИНТАКСИЧЕСКИЙ ЯКОРЬ" in free_role
    assert "QUERY-TYPE ROUTING (FREE)" not in free_role
    assert "3–4 пункта" not in free_role  # не тащим paid-аналитику на FREE
    assert "Compliance: FREE TIER" not in free_role  # хвост только в user inject
    assert "PREMIUM COPY PACK" in mini_role
    assert "<pre>" in mini_role
    assert "PREMIUM COPY PACK" in ultra_role

    mini_sys = get_role_prompt("standard", premium=True, tariff=TariffTier.MINI)
    assert "PREMIUM COPY PACK" in mini_sys
    assert "<pre>" in mini_sys
    assert "элитный эксперт" in mini_sys.lower()
    assert "query-type routing" in mini_sys.lower()
    assert "SYSTEM_ROLE" not in mini_sys


def test_charged_plan_preserves_tariff_for_prompt_branching() -> None:
    """Регрессия: после atomic_spend tariff не должен сбрасываться в FREE."""
    from services.billing.chat_pipeline import _blocked_plan
    from services.billing.types import ChatRoutePlan, CurrencyKind

    paid = ChatRoutePlan(
        model_id="google/gemini-2.5-flash",
        price_type=CurrencyKind.ENERGY,
        energy_cost=1,
        crystal_cost=1,
        is_expert_role=False,
        max_tokens=1500,
        use_premium_prompt=True,
        tariff=TariffTier.SMART,
    )
    blocked = _blocked_plan(paid, block_reason="zero_balance")
    assert blocked.tariff is TariffTier.SMART
    assert blocked.use_premium_prompt is True

    from content.chat_prompt import build_custom_role_prompt

    paid_role = build_custom_role_prompt("standard", blocked.tariff)
    # Если tariff потерян → FREE-хвост «в кавычках»; при SMART — copy-pack.
    assert "PREMIUM COPY PACK" in paid_role
    assert "Compliance: FREE TIER" not in paid_role
    assert "элитный эксперт" in paid_role
    assert "ДИНАМИЧЕСКИЙ ВЫБОР ФОРМАТА" in paid_role

    free_role = build_custom_role_prompt("standard", TariffTier.FREE)
    assert "FREE TIER" in free_role
    assert "===КНОПКИ===" in free_role
    assert "PREMIUM COPY PACK" not in free_role


def test_standard_max_tokens_free_vs_paid() -> None:
    from config import settings
    from services.billing.chat_pipeline import plan_text_chat
    from services.billing.types import UserBillingState

    free_user = UserBillingState(
        user_id=1,
        current_tariff=TariffTier.FREE,
        energy_free=30,
        energy_paid=0,
        crystals=0,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=False,
        photo_daily_date=None,
        photo_daily_count=0,
    )
    smart_user = UserBillingState(
        user_id=2,
        current_tariff=TariffTier.SMART,
        energy_free=0,
        energy_paid=1500,
        crystals=35,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=True,
        photo_daily_date=None,
        photo_daily_count=0,
    )
    free_plan = plan_text_chat(free_user, "standard")
    smart_plan = plan_text_chat(smart_user, "standard")
    from services.billing.chat_pipeline import _FREE_CHAT_MAX_OUTPUT_TOKENS

    assert free_plan.max_tokens == _FREE_CHAT_MAX_OUTPUT_TOKENS
    assert 400 <= free_plan.max_tokens <= 1000
    assert smart_plan.max_tokens == settings.openrouter_premium_max_output_tokens
    assert settings.openrouter_premium_max_output_tokens == 1500
    assert free_plan.use_premium_prompt is False
    assert smart_plan.use_premium_prompt is True
    assert free_plan.temperature is None
    assert smart_plan.temperature == 0.75

    mini_user = UserBillingState(
        user_id=3,
        current_tariff=TariffTier.MINI,
        energy_free=0,
        energy_paid=200,
        crystals=10,
        last_energy_reset=None,
        invited_by_id=None,
        first_purchase_done=True,
        photo_daily_date=None,
        photo_daily_count=0,
    )
    mini_plan = plan_text_chat(mini_user, "standard")
    assert mini_plan.temperature == 0.75
    # Не standard → температура не поднимается.
    assert plan_text_chat(smart_user, "blogger_content").temperature is None


def test_model_route_for_role_blogger_on_paid_tariff() -> None:
    model_id, _fallbacks = _model_route_for_role("blogger_content", TariffTier.MINI)
    assert model_id == PAID_CHAT_MODEL

    std_model, std_fb = _model_route_for_role("standard", TariffTier.MINI)
    assert std_model == PAID_CHAT_MODEL
    smart_model, smart_fb = _model_route_for_role("standard", TariffTier.SMART)
    assert smart_model == PAID_CHAT_MODEL
    assert "google/gemini-2.5-flash-lite" in smart_fb

    free_model, free_fb = _model_route_for_role("standard", TariffTier.FREE)
    # Живой кэш или аварийный :free — без платных ID.
    assert free_model.endswith(":free")
    assert all(m.endswith(":free") for m in (free_model, *free_fb))
    assert "meta-llama/llama-3.3-70b-instruct:free" not in free_fb
    assert "google/gemini-2.5-flash" not in free_fb
