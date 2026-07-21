"""Хвост compliance в последнем user-сообщении перед OpenRouter."""

from services.billing.chat_pipeline import (
    _model_route_for_role,
    inject_compliance_rules_into_last_user_message,
    prepare_openrouter_chat_messages,
)
from services.billing.pricing import PAID_CHAT_MODEL
from services.billing.types import TariffTier
from content.chat_prompt import BLOGGER_USER_COMPLIANCE_TAIL_MARKER, USER_COMPLIANCE_TAIL_MARKER


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
    assert "стиль FREE" in body
    assert "===КНОПКИ===" in body
    assert "СТИЛЬ ОТВЕТА" in body
    assert "премиум-комплаенс" not in body


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
    assert "премиум-комплаенс Стандарт" in body
    assert "Готово! Разные стили на выбор" in body
    assert "ИГНОРИРУЙ формат прошлых" in body or "ИГНОРИРУЙ" in body
    assert "<pre>" in body
    assert "Без блока ===КНОПКИ===" in body or "без блока ===КНОПКИ===" in body.lower() or "Без ===КНОПКИ===" in body
    assert "СТИЛЬ ОТВЕТА" not in body
    assert "Маршрут" not in body


def test_prepare_openrouter_collapses_assistant_history_for_paid_standard() -> None:
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
    assert len(payload) == 2
    assert payload[0]["role"] == "system"
    assert payload[1]["role"] == "user"
    assert "Напиши поздравление" in payload[1]["content"]
    assert "Вы можете создать" not in str(payload)
    assert "премиум-комплаенс" in payload[1]["content"]


def test_collapse_prior_assistant_keeps_only_system_and_last_user() -> None:
    from services.billing.chat_pipeline import (
        collapse_prior_assistant_for_copy_pack,
        prepare_openrouter_chat_messages,
    )

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

    payload = prepare_openrouter_chat_messages(
        [
            {"role": "system", "content": "COPY PACK"},
            {"role": "user", "content": "тема A"},
            {"role": "assistant", "content": "коуч-ответ"},
            {"role": "user", "content": "тема B"},
        ],
        use_premium_prompt=True,
        text_role="standard",
        chatcom_laconic=False,
    )
    assert [m["role"] for m in payload] == ["system", "user"]
    assert "тема B" in payload[1]["content"]
    assert "коуч-ответ" not in str(payload)


def test_paid_standard_uses_copy_pack_voice() -> None:
    from content.chat_prompt import build_custom_role_prompt, get_role_prompt
    from services.billing.types import TariffTier

    prompt = get_role_prompt("standard", premium=True, tariff=TariffTier.SMART)
    assert "PREMIUM COPY PACK" in prompt
    assert "элитный коммерческий копирайтер" in prompt
    assert "Готово! Разные стили на выбор" in prompt
    assert "<pre>" in prompt
    assert "Эмоциональный и душевный" in prompt
    assert "Ультра-короткий экспресс" in prompt
    assert "300–500" in prompt or "300-500" in prompt
    assert "1400" in prompt
    assert "ФОКУС НА ТЕКУЩЕМ ЗАПРОСЕ" in prompt
    assert "PROFESSIONAL LENGTH AND BUDGET CONTROL" not in prompt
    assert "ПРЕМИУМ NEUROMULE" not in prompt
    assert "Пример реплики" not in prompt
    assert "СТИЛЬ ОТВЕТА" not in prompt

    free_role = build_custom_role_prompt("standard", TariffTier.FREE)
    mini_role = build_custom_role_prompt("standard", TariffTier.MINI)
    ultra_role = build_custom_role_prompt("standard", TariffTier.ULTRA)
    assert "===КНОПКИ===" in free_role
    assert "Пример реплики" in free_role
    assert "ЕСТЕСТВЕННОСТЬ РЕЧИ" in free_role
    assert "PREMIUM COPY PACK" in mini_role
    assert "<pre>" in mini_role
    assert "PREMIUM COPY PACK" in ultra_role
    assert "СТИЛЬ ОТВЕТА" in free_role

    mini_sys = get_role_prompt("standard", premium=True, tariff=TariffTier.MINI)
    assert "PREMIUM COPY PACK" in mini_sys
    assert "<pre>" in mini_sys
    assert "коуч" not in mini_sys.lower() or "копирайтер" in mini_sys
    assert "СТИЛЬ ОТВЕТА" not in mini_sys
    assert "SYSTEM_ROLE" not in mini_sys


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
    assert free_plan.max_tokens == settings.openrouter_max_output_tokens
    assert smart_plan.max_tokens == settings.openrouter_premium_max_output_tokens
    assert settings.openrouter_premium_max_output_tokens == 1500
    assert free_plan.use_premium_prompt is False
    assert smart_plan.use_premium_prompt is True


def test_model_route_for_role_blogger_on_paid_tariff() -> None:
    model_id, _fallbacks = _model_route_for_role("blogger_content", TariffTier.MINI)
    assert model_id == PAID_CHAT_MODEL

    std_model, std_fb = _model_route_for_role("standard", TariffTier.MINI)
    assert std_model == PAID_CHAT_MODEL
    smart_model, smart_fb = _model_route_for_role("standard", TariffTier.SMART)
    assert smart_model == PAID_CHAT_MODEL
    assert "google/gemini-2.5-flash-lite" in smart_fb

    free_model, free_fb = _model_route_for_role("standard", TariffTier.FREE)
    # openrouter/free принудительно заменяется: роутер отдаёт content-safety.
    assert free_model.endswith(":free")
    assert free_model != "openrouter/free"
    assert "google/gemma-4-26b-a4b-it:free" in (free_model, *free_fb)
    assert "openai/gpt-oss-20b:free" in free_fb or free_model == "openai/gpt-oss-20b:free"
    assert "meta-llama/llama-3.2-3b-instruct:free" not in free_fb
    assert "meta-llama/llama-3.3-70b-instruct:free" not in free_fb
