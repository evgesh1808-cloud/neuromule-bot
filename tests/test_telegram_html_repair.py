from services.telegram_safe_text import prepare_telegram_html_text, repair_telegram_html


def test_repair_closes_unclosed_bold() -> None:
    assert repair_telegram_html("<b>текст") == "<b>текст</b>"


def test_prepare_handles_list_and_tags() -> None:
    raw = "• <b>Заголовок:</b>\n• пункт\n<b>обрыв"
    out = prepare_telegram_html_text(raw)
    assert "• пункт" not in out
    assert "пункт" in out
    assert out.endswith("</b>")
