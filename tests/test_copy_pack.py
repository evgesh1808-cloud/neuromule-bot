"""Тесты валидации Premium Standard copy-pack."""

from __future__ import annotations

from services.copy_pack import (
    COPY_PACK_ASSISTANT_PREFIX,
    convert_md_fences_to_pre,
    is_premium_copy_pack_reply,
    looks_like_coach_reply,
    merge_copy_pack_prefix,
    normalize_copy_pack_reply,
)
from services.use_cases.chat_turn import clean_markdown_to_html


def test_is_premium_copy_pack_reply_accepts_valid() -> None:
    text = (
        "Готово! Разные варианты на выбор (нажмите на текст, чтобы скопировать):\n\n"
        "🎉 <b>Трогательное и душевное</b>\n"
        "<pre>\nА\n</pre>\n\n"
        "🥂 <b>Короткое СМС-поздравление</b>\n"
        "<pre>\nБ\n</pre>\n\n"
        "🚀 <b>Драйвовое и молодежное</b>\n"
        "<pre>\nВ\n</pre>\n\n"
        "💼 <b>Уважительное/Официальное</b>\n"
        "<pre>\nГ\n</pre>\n"
    )
    assert is_premium_copy_pack_reply(text) is True


def test_is_premium_copy_pack_without_classic_opener() -> None:
    from services.copy_pack import suppress_suggested_replies_for_answer

    text = (
        "Вот варианты поздравления:\n\n"
        "🎉 <b>Трогательное</b>\n<pre>\nА\n</pre>\n\n"
        "🥂 <b>СМС</b>\n<pre>\nБ\n</pre>\n\n"
        "🚀 <b>Драйв</b>\n<pre>\nВ\n</pre>\n\n"
        "===КНОПКИ===\n"
        "Трогательное и душевное\n"
        "Короткое СМС-поздравление\n"
        "Драйвовое\n"
    )
    assert is_premium_copy_pack_reply(text) is True
    assert suppress_suggested_replies_for_answer(text) is True


def test_is_premium_copy_pack_reply_accepts_legacy_opener() -> None:
    text = (
        "Готово! Разные стили на выбор (нажмите на текст, чтобы скопировать):\n\n"
        "🎉 <b>Трогательное</b>\n<pre>\nА\n</pre>\n\n"
        "🥂 <b>Короткое</b>\n<pre>\nБ\n</pre>\n\n"
        "🚀 <b>Драйвовое</b>\n<pre>\nВ\n</pre>\n"
    )
    assert is_premium_copy_pack_reply(text) is True


def test_is_premium_copy_pack_reply_rejects_coach() -> None:
    text = (
        "Вы можете создать тёплое поздравление.\n"
        "1. Начните с обращения\n"
        "2. Добавьте пожелание\n"
        "📋 Пример поздравления\n"
        "С днём рождения!"
    )
    assert is_premium_copy_pack_reply(text) is False
    assert looks_like_coach_reply(text) is True


def test_merge_copy_pack_prefix_on_continuation() -> None:
    continuation = (
        "🎉 <b>Трогательное и душевное</b>\n<pre>\nМилая, с днём рождения!\n</pre>\n\n"
        "🥂 <b>Короткое СМС</b>\n<pre>\nУспехов!\n</pre>\n\n"
        "🚀 <b>Драйвовое</b>\n<pre>\nС ДР!\n</pre>"
    )
    merged = merge_copy_pack_prefix(COPY_PACK_ASSISTANT_PREFIX, continuation)
    assert merged.startswith("Готово! Разные варианты")
    assert "<pre>" in merged
    assert "Эмоциональный" not in COPY_PACK_ASSISTANT_PREFIX
    assert is_premium_copy_pack_reply(merged) is True


def test_convert_md_fences_to_pre() -> None:
    raw = "intro\n```\nТекст один\n```\n```\nТекст два\n```"
    converted = convert_md_fences_to_pre(raw)
    assert converted.count("<pre>") == 2
    assert "```" not in converted


def test_clean_markdown_preserves_pre_blocks() -> None:
    text = (
        "Готово! Разные варианты на выбор (нажмите на текст, чтобы скопировать):\n"
        "<pre>\nНе трогай *это*\n</pre>\n"
        "<pre>\nВторой\n</pre>\n"
        "<pre>\nТретий\n</pre>"
    )
    out = clean_markdown_to_html(text)
    assert "<pre>" in out
    assert "Не трогай *это*" in out


def test_normalize_copy_pack_reply_strips() -> None:
    assert normalize_copy_pack_reply("  hi  ") == "hi"
