from services.telegram_safe_text import sanitize_telegram_plain_text


def test_sanitize_strips_html_tags() -> None:
    raw = "Привет <b>мир</b> и <script>x</script>"
    assert "<" not in sanitize_telegram_plain_text(raw)
    assert "Привет" in sanitize_telegram_plain_text(raw)


def test_sanitize_truncates_long_text() -> None:
    assert len(sanitize_telegram_plain_text("x" * 5000, max_len=100)) <= 100
