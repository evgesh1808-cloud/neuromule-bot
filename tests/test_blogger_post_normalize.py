"""Тесты нормализации «плоского» ответа блогера без ===-маркеров."""

from __future__ import annotations

from services.blogger_image_prompt import sanitize_blogger_image_prompt_for_imagen
from services.blogger_post_parser import (
    MISSING_SECTION_PLACEHOLDER,
    BloggerPostParsed,
    format_blogger_display_html,
    is_blogger_response_degraded,
    normalize_blogger_raw_output,
    parse_blogger_post,
    reassemble_blogger_sections,
    repair_blogger_telegram_html,
)

_ALL_SECTION_KEYS = (
    "ХУКИ",
    "ТЕЛО ПОСТА",
    "ПРИЗЫВЫ К ДЕЙСТВИЮ",
    "ХЭШТЕГИ",
    "ПРОМПТ ДЛЯ КАРТИНКИ",
)

_FLAT_DOG_POST = """[Вариант 1 (Интрига)]: ⚡ Исчезновение на реке

[Вариант 2 (Боль аудитории)]: 💔 Кошмар туриста

[Вариант 3 (Хайп)]: 🔥 Утро без Стива

Адреналин бьёт ключом, когда любимый четвероногий друг растворяется в лесу.

[Вариант А (Вовлечение)]: 💬 А с вами случались истории?

[Вариант Б (Коммерческий)]: 🛡️ Защитите своего любимца

[Тематические]: #Собаки #Поход
[Навигационные]: #Блог_инсайт

A professional cinematic photo of lost dog in forest, 4k --ar 16:9
"""


def _assert_all_section_keys(sections: dict[str, str]) -> None:
    assert set(sections.keys()) == set(_ALL_SECTION_KEYS)
    for key in _ALL_SECTION_KEYS:
        assert isinstance(sections[key], str)


def test_normalize_returns_dict_with_all_section_keys() -> None:
    sections = normalize_blogger_raw_output("")
    _assert_all_section_keys(sections)
    assert sections["ХУКИ"] == MISSING_SECTION_PLACEHOLDER


def test_normalize_flat_blogger_post_adds_section_markers() -> None:
    sections = normalize_blogger_raw_output(_FLAT_DOG_POST)
    _assert_all_section_keys(sections)
    normalized = reassemble_blogger_sections(sections)
    assert normalized.startswith("===ХУКИ===")
    assert "===ТЕЛО ПОСТА===" in normalized
    assert "===ПРИЗЫВЫ К ДЕЙСТВИЮ===" in normalized
    assert "===ХЭШТЕГИ===" in normalized
    assert "===ПРОМПТ ДЛЯ КАРТИНКИ===" in normalized

    parsed = parse_blogger_post(normalized)
    assert parsed.body is not None
    assert "Адреналин" in parsed.body
    assert parsed.hashtags is not None
    assert "#Собаки" in parsed.hashtags
    assert parsed.image_prompt is not None
    assert "lost dog" in parsed.image_prompt
    assert "===ХЭШТЕГИ===" not in parsed.display_plain()


def test_repair_blogger_telegram_html_converts_markdown_bold() -> None:
    assert repair_blogger_telegram_html("**ключевой тезис** в тексте") == (
        "<b>ключевой тезис</b> в тексте"
    )
    assert repair_blogger_telegram_html("__важно__") == "<b>важно</b>"


def test_repair_blogger_telegram_html_converts_markdown_headers() -> None:
    assert repair_blogger_telegram_html("### Главный инсайт\nТекст абзаца") == (
        "<b>Главный инсайт</b>\nТекст абзаца"
    )
    assert repair_blogger_telegram_html("## Подзаголовок") == "<b>Подзаголовок</b>"


def test_repair_blogger_telegram_html_closes_unclosed_b_tags() -> None:
    assert repair_blogger_telegram_html("<b>тезис без закрытия") == "<b>тезис без закрытия</b>"
    assert repair_blogger_telegram_html("<b>a</b> и <b>b") == "<b>a</b> и <b>b</b>"


def test_normalize_repairs_markdown_in_structured_body() -> None:
    raw = """===ХУКИ===
Хук

===ТЕЛО ПОСТА===
**Главный инсайт** о продукте.

===ПРИЗЫВЫ К ДЕЙСТВИЮ===
CTA
"""
    sections = normalize_blogger_raw_output(raw)
    _assert_all_section_keys(sections)
    assert "<b>Главный инсайт</b>" in sections["ТЕЛО ПОСТА"]
    assert "**" not in sections["ТЕЛО ПОСТА"]
    assert sections["ХЭШТЕГИ"] == MISSING_SECTION_PLACEHOLDER
    assert sections["ПРОМПТ ДЛЯ КАРТИНКИ"] == MISSING_SECTION_PLACEHOLDER


def test_normalize_unwraps_fenced_codeblock() -> None:
    raw = """```html
Вот ваш пост:

===ХУКИ===
Хук один

===ТЕЛО ПОСТА===
Текст тела

===ПРИЗЫВЫ К ДЕЙСТВИЮ===
CTA
```"""
    sections = normalize_blogger_raw_output(raw)
    _assert_all_section_keys(sections)
    assert sections["ХУКИ"] == "Хук один"
    assert sections["ТЕЛО ПОСТА"] == "Текст тела"
    assert "Вот ваш пост" not in reassemble_blogger_sections(sections)


def test_normalize_loose_headers_without_triple_equals() -> None:
    raw = """Конечно! Ниже готовый пост.

ХУКИ
Вариант заголовка

ТЕЛО ПОСТА
Основной **инсайт** о продукте.

ПРИЗЫВЫ К ДЕЙСТВИЮ
Подпишись на канал

ХЭШТЕГИ
#AI #Tech

ПРОМПТ ДЛЯ КАРТИНКИ
A professional cinematic photo of office desk, 4k --ar 16:9
"""
    sections = normalize_blogger_raw_output(raw)
    _assert_all_section_keys(sections)
    assert sections["ХУКИ"] == "Вариант заголовка"
    assert "<b>инсайт</b>" in sections["ТЕЛО ПОСТА"]
    assert "#AI" in sections["ХЭШТЕГИ"]
    assert "office desk" in sections["ПРОМПТ ДЛЯ КАРТИНКИ"]


def test_normalize_partial_sections_fill_missing_with_placeholders() -> None:
    raw = """===ХУКИ===
Только хук

===ТЕЛО ПОСТА===
Только тело
"""
    sections = normalize_blogger_raw_output(raw)
    _assert_all_section_keys(sections)
    assert sections["ХУКИ"] == "Только хук"
    assert sections["ТЕЛО ПОСТА"] == "Только тело"
    assert sections["ПРИЗЫВЫ К ДЕЙСТВИЮ"] == MISSING_SECTION_PLACEHOLDER
    assert sections["ХЭШТЕГИ"] == MISSING_SECTION_PLACEHOLDER
    assert sections["ПРОМПТ ДЛЯ КАРТИНКИ"] == MISSING_SECTION_PLACEHOLDER

    display = BloggerPostParsed(sections=sections).display_plain()
    assert "Только хук" in display
    assert "Только тело" in display
    assert MISSING_SECTION_PLACEHOLDER not in display


def test_normalize_unparsed_text_goes_to_body_section() -> None:
    sections = normalize_blogger_raw_output("Просто сплошной текст без маркеров.")
    _assert_all_section_keys(sections)
    assert sections["ХУКИ"] == MISSING_SECTION_PLACEHOLDER
    assert sections["ТЕЛО ПОСТА"] == "Просто сплошной текст без маркеров."
    assert sections["ПРИЗЫВЫ К ДЕЙСТВИЮ"] == MISSING_SECTION_PLACEHOLDER


def test_sanitize_blogger_image_prompt_strips_abstract_phrases() -> None:
    raw = (
        "A professional cinematic photo of businessman at desk, "
        "representing financial growth, warm office light, 4k --ar 16:9"
    )
    cleaned = sanitize_blogger_image_prompt_for_imagen(raw)
    assert "representing" not in cleaned.lower()
    assert "businessman" in cleaned.lower()
    assert cleaned.startswith("A professional cinematic photo of")

    ru_raw = (
        "Профессиональное фото офиса с графиками, символизирующее рост прибыли, "
        "мягкий свет"
    )
    ru_cleaned = sanitize_blogger_image_prompt_for_imagen(ru_raw)
    assert "символизиру" not in ru_cleaned.lower()
    assert "офиса" in ru_cleaned.lower() or "office" in ru_cleaned.lower()


def test_sanitize_blogger_image_prompt_strips_banned_abstract_terms() -> None:
    raw = (
        "A professional cinematic photo of a chart symbolizing success and future hope, "
        "4k --ar 16:9"
    )
    cleaned = sanitize_blogger_image_prompt_for_imagen(raw)
    low = cleaned.lower()
    assert "symbolizing" not in low
    assert "success" not in low
    assert "future" not in low
    assert "hope" not in low


def test_strip_optimizer_preamble() -> None:
    from services.blogger_image_prompt import _strip_optimizer_preamble

    assert _strip_optimizer_preamble(
        'Here is your prompt: "A professional cinematic photo of a desk lamp"'
    ) == "A professional cinematic photo of a desk lamp"


def test_imagen4_optimizer_system_prompt_contains_core_rules() -> None:
    from services.blogger_image_prompt import IMAGEN4_OPTIMIZER_SYSTEM_PROMPT

    low = IMAGEN4_OPTIMIZER_SYSTEM_PROMPT.lower()
    assert "symbolizing" in low
    assert "representing" in low
    assert "concept of" in low
    assert "single line" in low or "one line" in low


def test_is_blogger_response_degraded_detects_header_echo() -> None:
    sections = normalize_blogger_raw_output("===ХУКИ===\nХуки\n\n===ТЕЛО ПОСТА===\nтело поста")
    assert is_blogger_response_degraded(sections) is True


def test_is_blogger_response_degraded_accepts_full_post() -> None:
    sections = normalize_blogger_raw_output(_FLAT_DOG_POST)
    assert is_blogger_response_degraded(sections) is False


def test_format_blogger_display_html_includes_section_labels() -> None:
    sections = normalize_blogger_raw_output(_FLAT_DOG_POST)
    html = format_blogger_display_html(sections)
    assert "<b>💡 Варианты ярких заголовков:</b>" in html
    assert "<b>✍️ Текст поста:</b>" in html
    assert "Адреналин" in html
