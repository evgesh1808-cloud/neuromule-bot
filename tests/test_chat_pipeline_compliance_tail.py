"""Хвост compliance в последнем user-сообщении перед OpenRouter."""

from services.billing.chat_pipeline import (
    _model_route_for_role,
    inject_compliance_rules_into_last_user_message,
    prepare_openrouter_chat_messages,
)
from services.billing.pricing import FREE_CHAT_MODEL, PAID_CHAT_MODEL
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


def test_model_route_for_role_blogger_on_paid_tariff() -> None:
    model_id, _fallbacks = _model_route_for_role("blogger_content", TariffTier.MINI)
    assert model_id == PAID_CHAT_MODEL

    std_model, _ = _model_route_for_role("standard", TariffTier.MINI)
    assert std_model == FREE_CHAT_MODEL
