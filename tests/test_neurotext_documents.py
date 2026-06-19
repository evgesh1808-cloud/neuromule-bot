"""Тесты документного ввода в Нейротексте (.txt / .csv / .pdf / .docx)."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.file_processor import DocumentTooBigError
from services.neurotext_media import (
    NeurotextUnsupportedDocumentError,
    PdfScanUnreadableError,
    merge_document_caption_and_text,
    telegram_document_to_neurotext_payload,
    telegram_document_to_prompt_text,
)


def test_merge_document_caption_and_text() -> None:
    assert merge_document_caption_and_text("Сделай выжимку", "Текст файла") == (
        "Сделай выжимку\n\nТекст файла"
    )
    assert merge_document_caption_and_text("", "Только файл") == "Только файл"
    assert merge_document_caption_and_text("Только подпись", "") == "Только подпись"


@pytest.mark.asyncio
async def test_telegram_document_to_prompt_text_txt() -> None:
    bot = AsyncMock()
    document = SimpleNamespace(
        file_id="doc1",
        file_name="notes.txt",
        file_size=12,
    )
    payload = "Hello   world\u200b\n\n\nBody".encode("utf-8")

    with patch(
        "services.file_processor.download_telegram_document_to_buffer",
        new=AsyncMock(return_value=__import__("io").BytesIO(payload)),
    ):
        text = await telegram_document_to_prompt_text(bot, document, max_chars=10_000)

    assert text == "Hello world\n\nBody"


@pytest.mark.asyncio
async def test_telegram_document_unsupported_suffix() -> None:
    bot = AsyncMock()
    document = SimpleNamespace(file_id="d", file_name="sheet.xlsx", file_size=1)
    with pytest.raises(NeurotextUnsupportedDocumentError):
        await telegram_document_to_prompt_text(bot, document, max_chars=1000)


@pytest.mark.asyncio
async def test_telegram_document_propagates_too_big() -> None:
    bot = AsyncMock()
    document = SimpleNamespace(file_id="d", file_name="big.pdf", file_size=99_999_999)

    with patch(
        "services.file_processor.download_telegram_document_to_buffer",
        new=AsyncMock(side_effect=DocumentTooBigError(99_999_999)),
    ):
        with pytest.raises(DocumentTooBigError):
            await telegram_document_to_prompt_text(bot, document, max_chars=1000)


@pytest.mark.asyncio
async def test_telegram_document_accepts_docx_suffix() -> None:
    bot = AsyncMock()
    document = SimpleNamespace(
        file_id="docx1",
        file_name="report.docx",
        file_size=100,
    )
    with patch(
        "services.file_processor.download_telegram_document_to_buffer",
        new=AsyncMock(return_value=BytesIO(b"fake-docx-bytes")),
    ), patch(
        "services.file_processor.extract_text_from_document",
        new=AsyncMock(return_value="Текст из Word"),
    ) as extract_mock:
        text = await telegram_document_to_prompt_text(bot, document, max_chars=5000)

    assert text == "Текст из Word"
    extract_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_chat_turn_table_role_with_document(repo_module) -> None:
    from config import Settings, settings as app_settings
    from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn
    from tests.conftest import TEST_ADMIN_IDS

    uid = TEST_ADMIN_IDS[0]
    await repo_module.ensure_user(uid)
    object.__setattr__(app_settings, "god_mode_enabled", True)

    fake_plan = SimpleNamespace(
        blocked=False,
        block_reason="",
        model_id="google/gemini-2.5-flash",
        max_tokens=640,
        use_premium_prompt=True,
    )
    billing_result = SimpleNamespace(
        plan=fake_plan,
        charge_id="god_mode_skip",
        effective_role_id="table_generator",
        notice=None,
    )
    captured_prompt: list[str] = []

    async def _fake_ask(_settings, messages, **kwargs):
        user_msg = messages[-1]["content"]
        captured_prompt.append(user_msg if isinstance(user_msg, str) else str(user_msg))
        return "Ответ по документу"

    with patch(
        "services.use_cases.chat_turn.allow_request",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.use_cases.chat_turn.billing.resolve_and_charge_text_chat",
        new=AsyncMock(return_value=billing_result),
    ), patch(
        "services.use_cases.chat_turn.dialog_append",
        new=AsyncMock(),
    ), patch(
        "services.use_cases.chat_turn.conv.build_openrouter_messages",
        new=AsyncMock(
            return_value=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "placeholder"},
            ]
        ),
    ), patch(
        "services.use_cases.chat_turn.ask_ai_messages",
        new=AsyncMock(side_effect=_fake_ask),
    ), patch(
        "services.use_cases.chat_turn.commit_assistant_turn_queued",
        new=AsyncMock(),
    ), patch(
        "services.use_cases.chat_turn.conv.schedule_memory_refresh",
    ):
        s = Settings(tg_token="x", openrouter_key="y", gemini_api_key="z")
        doc_body = "Строка из CSV\n1;2;3"
        result = await run_chat_turn(
            s,
            uid,
            merge_document_caption_and_text("Проанализируй", doc_body),
            dialog_user_text="[📄 data.csv]",
            text_role="table_generator",
        )
        assert result.outcome is ChatTurnOutcome.SUCCESS
        assert result.effective_text_role == "table_generator"
        assert "Проанализируй" in captured_prompt[0]
        assert "1;2;3" in captured_prompt[0]


@pytest.mark.asyncio
async def test_telegram_pdf_scan_falls_back_to_vision() -> None:
    bot = AsyncMock()
    document = SimpleNamespace(
        file_id="pdf_scan",
        file_name="scan.pdf",
        file_size=500,
    )
    with patch(
        "services.file_processor.download_telegram_document_to_buffer",
        new=AsyncMock(return_value=BytesIO(b"%PDF-empty-scan-mock")),
    ), patch(
        "services.file_processor.extract_text_from_pdf",
        return_value="",
    ), patch(
        "services.file_processor.pdf_first_page_to_data_url",
        return_value="data:image/png;base64,abc",
    ):
        payload = await telegram_document_to_neurotext_payload(
            bot, document, max_chars=5000
        )

    assert payload.needs_vision
    assert payload.scan_image_data_url == "data:image/png;base64,abc"
    assert payload.extracted_text == ""


@pytest.mark.asyncio
async def test_telegram_pdf_scan_unreadable_raises() -> None:
    bot = AsyncMock()
    document = SimpleNamespace(
        file_id="pdf_bad",
        file_name="broken.pdf",
        file_size=100,
    )
    with patch(
        "services.file_processor.download_telegram_document_to_buffer",
        new=AsyncMock(return_value=BytesIO(b"%PDF")),
    ), patch(
        "services.file_processor.extract_text_from_pdf",
        return_value="",
    ), patch(
        "services.file_processor.pdf_first_page_to_data_url",
        return_value=None,
    ):
        with pytest.raises(PdfScanUnreadableError):
            await telegram_document_to_neurotext_payload(
                bot, document, max_chars=5000
            )
