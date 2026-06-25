from services.dialog_sanitize import (
    compact_table_history_note,
    sanitize_dialog_content_for_chat,
)


def test_sanitize_json_table_blob() -> None:
    blob = '{"title":"WB отчёт","headers":["A"],"rows":[["1"],["2"],["3"]]}'
    out = sanitize_dialog_content_for_chat(blob)
    assert "WB отчёт" in out
    assert "3 строк" in out
    assert len(out) < 200


def test_sanitize_preserves_short_text() -> None:
    assert sanitize_dialog_content_for_chat("Привет") == "Привет"


def test_wb_history_note() -> None:
    note = compact_table_history_note(
        title="Реализация",
        row_count=42,
        table_subrole="wb_ozon_finance",
    )
    assert "WB/Ozon" in note
    assert "42" in note
