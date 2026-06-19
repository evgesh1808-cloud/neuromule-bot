from services.telegram_safe_text import markdown_to_html, normalize_telegram_list_markup


def test_header_strips_bullet_without_readding() -> None:
    raw = "• <b>Ключевые тезисы:</b>\n• • Первый пункт\n• Второй пункт"
    out = normalize_telegram_list_markup(raw)
    assert out.startswith("<b>Ключевые тезисы:</b>")
    assert "• •" not in out
    assert "• Первый пункт" not in out
    assert "Первый пункт" in out
    assert "Второй пункт" in out


def test_markdown_header_without_list_bullet() -> None:
    raw = "**Ключевые тезисы:**\n- первый пункт\n- второй"
    out = markdown_to_html(raw)
    assert out.startswith("<b>Ключевые тезисы:</b>")
    assert "• первый пункт" not in out
    assert "первый пункт" in out
    assert "• <b>" not in out


def test_markdown_dash_header_line_not_bulleted() -> None:
    """Модель часто шлёт «- **Заголовок:**» — не превращаем в bullet-список."""
    raw = "- **Ключевые тезисы:**\n- первый пункт"
    out = markdown_to_html(raw)
    assert out.splitlines()[0] == "<b>Ключевые тезисы:</b>"
    assert "• первый пункт" not in out
    assert "первый пункт" in out


def test_plain_header_no_bullet_added() -> None:
    raw = "Итог:\n• один пункт"
    out = normalize_telegram_list_markup(raw)
    assert out.splitlines()[0] == "Итог:"
    assert "• один пункт" not in out
    assert "один пункт" in out


def test_numbered_list_preserved() -> None:
    raw = "  <b>1. Раздел</b>\n  1. Первый пункт\n  2. Второй пункт"
    out = normalize_telegram_list_markup(raw)
    assert out.splitlines()[0] == "<b>1. Раздел</b>"
    assert out.splitlines()[1] == "1. Первый пункт"
    assert out.splitlines()[2] == "2. Второй пункт"


def test_question_answer_layout_preserved() -> None:
    raw = "<b>💬 Вопрос: Как начать?</b>\nОтвет: С малого шага."
    out = normalize_telegram_list_markup(raw)
    assert out == raw
