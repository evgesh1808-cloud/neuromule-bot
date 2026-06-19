"""Обработка входящих сообщений Нейротекста (текст / фото / документ)."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from content import messages as msg
from platforms.handlers import deps
from platforms.neurotext_flow import ensure_neurotext_waiting_state, send_neurotext_role_menu
from platforms.telegram_chat_action import chat_action_loop
from platforms.telegram_chat_stream import create_throttled_stream_reply
from platforms.table_generator_delivery import send_table_generator_pack
from platforms.telegram_chunks import answer_chat_text
from platforms.telegram_quote import (
    build_quoted_user_prompt,
    has_neurotext_message_input,
    resolve_neurotext_quote_input,
)
from platforms.telegram_states import UserFlow
from services import payments_catalog as paycat
from services.billing.types import TariffTier
from services.file_processor import (
    DocumentTooBigError,
    TXT_DOCUMENT_TOO_BIG,
    download_telegram_document_to_buffer,
    read_xlsx_rows_from_bytes,
)
from services.god_mode import billing_bypass
from services.neurotext_media import (
    NEUROTEXT_DOCUMENT_SUFFIXES,
    PDF_SCAN_VISION_PROMPT,
    NeurotextPhotoTooBigError,
    NeurotextUnsupportedDocumentError,
    PdfScanUnreadableError,
    merge_document_caption_and_text,
    telegram_document_to_neurotext_payload,
    telegram_photo_to_data_url,
)
from services.repository import get_user_row
from services.table_markdown import rows_to_markdown_table
from services.use_cases.chat_turn import ChatTurnOutcome, run_chat_turn

logger = logging.getLogger(__name__)


async def _table_xlsx_allowed(user_id: int) -> bool:
    if billing_bypass(user_id):
        return True
    row = await get_user_row(user_id)
    return TariffTier.from_db(row.tariff) is not TariffTier.FREE


async def _reply_chat_turn_result(
    message: Message,
    result,
    *,
    stream_cb,
    table_context: str = "",
) -> None:
    if result.outcome is ChatTurnOutcome.SUCCESS:
        if result.user_notice:
            await message.answer(
                result.user_notice,
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        if stream_cb is None:
            if result.table_raw_json:
                delivered = await send_table_generator_pack(
                    message,
                    result.table_raw_json,
                    context_text=table_context,
                    report_id=result.table_report_id,
                )
                if not delivered:
                    await message.answer(
                        "⚠️ Не удалось разобрать таблицу из ответа модели. "
                        "Попробуйте переформулировать запрос.",
                        parse_mode=ParseMode.HTML,
                    )
            elif result.assistant_message:
                await answer_chat_text(message, result.assistant_message, settings)
        return
    if result.outcome is ChatTurnOutcome.EMPTY_INPUT:
        await message.answer(msg.TXT_CHAT_EMPTY)
        return
    if result.outcome is ChatTurnOutcome.CONTEXT_TOO_LARGE:
        await message.answer(msg.TXT_CHAT_CONTEXT_TOO_LARGE)
        return
    if result.outcome is ChatTurnOutcome.RATE_LIMITED:
        await message.answer(msg.TXT_CHAT_RATE_LIMIT)
        return
    if result.outcome is ChatTurnOutcome.ROLE_NOT_ALLOWED:
        await message.answer(
            msg.TXT_PREMIUM_ROLE_LOCKED,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if result.outcome is ChatTurnOutcome.INSUFFICIENT_BALANCE:
        await message.answer(
            msg.TXT_CHAT_ZERO_BALANCE_PREMIUM,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if result.outcome is ChatTurnOutcome.AI_FAILED:
        await message.answer(msg.TXT_CHAT_AI_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return
    await message.answer(msg.TXT_GEN_JOB_FAILED)


async def handle_neurotext_user_message(
    message: Message,
    state: FSMContext,
    *,
    keep_waiting_state: bool = True,
) -> None:
    """Единая точка: текст, фото или документ → ``run_chat_turn``."""
    is_photo = bool(message.photo)
    is_document = bool(message.document)

    if (
        not is_photo
        and not is_document
        and not has_neurotext_message_input(message)
    ):
        await send_neurotext_role_menu(message, state)
        return

    await ensure_neurotext_waiting_state(state)
    data = await state.get_data()
    role_id = str(data.get("text_role") or "standard")
    uid = message.from_user.id

    user_image_data_url: str | None = None
    try:
        if is_document:
            doc = message.document
            file_name = (doc.file_name or "document").strip()
            suffix = Path(file_name).suffix.lower()
            caption = (message.caption or "").strip()
            status = await message.answer(
                "📎 <b>Читаю документ…</b>",
                parse_mode=ParseMode.HTML,
            )
            if suffix == ".xlsx" and role_id == "table_generator":
                if not await _table_xlsx_allowed(uid):
                    await status.edit_text(
                        "⛔ Редактирование Excel доступно с тарифа <b>MINI</b> и выше. "
                        "Открой «🚀 Тарифы» для подключения.",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                buffer = await download_telegram_document_to_buffer(deps.bot(), doc)
                rows = read_xlsx_rows_from_bytes(buffer.getvalue())
                md_table = rows_to_markdown_table(rows)
                if not md_table.strip():
                    await status.edit_text("⚠️ Excel-файл пустой или не содержит данных.")
                    return
                instruction = caption or "Обнови таблицу по данным Excel и верни Markdown-таблицу."
                raw_user_text = f"{instruction}\n\nТекущие данные Excel:\n{md_table}"
                dialog_text = caption or f"[📊 Excel {file_name}]"
            elif suffix in NEUROTEXT_DOCUMENT_SUFFIXES:
                doc_payload = await telegram_document_to_neurotext_payload(
                    deps.bot(),
                    doc,
                    max_chars=settings.chat_max_message_chars,
                )
                if doc_payload.needs_vision:
                    try:
                        await status.edit_text(
                            "📷 <b>Распознаю скан PDF…</b>",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass
                    user_image_data_url = doc_payload.scan_image_data_url
                    scan_prompt = caption or PDF_SCAN_VISION_PROMPT
                    raw_user_text = scan_prompt
                    dialog_text = caption or f"[📄 скан {file_name}]"
                else:
                    raw_user_text = merge_document_caption_and_text(
                        caption,
                        doc_payload.extracted_text,
                    )
                    if not raw_user_text.strip():
                        await status.edit_text(msg.TXT_CHAT_EMPTY)
                        return
                    dialog_text = caption or f"[📄 {file_name}]"
            else:
                try:
                    await status.delete()
                except Exception:
                    pass
                if suffix == ".xlsx":
                    await message.answer(
                        "⚠️ Файлы <b>.xlsx</b> принимаются только в роли <b>📊 Таблицы</b>.",
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await message.answer(
                        "⚠️ Поддерживаются документы "
                        "<b>.txt</b>, <b>.csv</b>, <b>.pdf</b>, <b>.docx</b> "
                        "и <b>.xlsx</b> (только в роли Таблицы).",
                        parse_mode=ParseMode.HTML,
                    )
                return
            try:
                await status.delete()
            except Exception:
                pass
        elif is_photo:
            caption = (message.caption or "").strip()
            status = await message.answer(
                "📷 <b>Анализирую фото…</b>",
                parse_mode=ParseMode.HTML,
            )
            user_image_data_url = await telegram_photo_to_data_url(
                deps.bot(),
                message.photo[-1],
            )
            raw_user_text = caption or "Опиши изображение и ответь по выбранной роли NeuroMule."
            dialog_text = caption or "[📷 Фото]"
            try:
                await status.delete()
            except Exception:
                pass
        else:
            quoted_text, user_text = resolve_neurotext_quote_input(message)
            raw_user_text = build_quoted_user_prompt(user_text, quoted_text)
            dialog_text = user_text[: settings.chat_max_message_chars] if quoted_text else None
    except DocumentTooBigError:
        await message.answer(TXT_DOCUMENT_TOO_BIG, parse_mode=ParseMode.HTML)
        return
    except NeurotextUnsupportedDocumentError:
        await message.answer(
            "⚠️ Поддерживаются документы "
            "<b>.txt</b>, <b>.csv</b>, <b>.pdf</b>, <b>.docx</b> "
            "и <b>.xlsx</b> (только в роли Таблицы).",
            parse_mode=ParseMode.HTML,
        )
        return
    except NeurotextPhotoTooBigError:
        await message.answer(
            "⚠️ Фото слишком большое (лимит 5 МБ). Отправь сжатое изображение.",
            parse_mode=ParseMode.HTML,
        )
        return
    except PdfScanUnreadableError:
        await message.answer(
            "⚠️ PDF похож на скан, но не удалось подготовить страницу для распознавания. "
            "Попробуйте отправить фото страницы или текстовый .txt.",
            parse_mode=ParseMode.HTML,
        )
        return
    except RuntimeError as exc:
        logger.warning("neurotext media extract failed uid=%s: %s", uid, exc)
        await message.answer(
            "⚠️ Не удалось прочитать файл на сервере. "
            "Попробуйте отправить .txt или .csv.",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception:
        logger.exception("neurotext media unexpected error uid=%s", uid)
        await message.answer(
            "⚠️ Не удалось обработать вложение. Попробуйте ещё раз или отправьте текст.",
            parse_mode=ParseMode.HTML,
        )
        return

    max_len = settings.chat_max_message_chars
    raw = raw_user_text[:max_len]
    if dialog_text is not None:
        dialog_text = dialog_text[:max_len]

    use_stream = (
        settings.telegram_chat_streaming
        and not is_photo
        and not user_image_data_url
        and role_id != "table_generator"
    )

    table_context = raw if role_id == "table_generator" else ""

    async with chat_action_loop(deps.bot(), message.chat.id, "typing"):
        stream_cb = (
            create_throttled_stream_reply(message, deps.bot(), settings)
            if use_stream
            else None
        )
        result = await run_chat_turn(
            settings,
            uid,
            raw,
            dialog_user_text=dialog_text,
            user_image_data_url=user_image_data_url,
            stream_callback=stream_cb,
            text_role=role_id,
        )

    if keep_waiting_state and result.outcome is ChatTurnOutcome.SUCCESS:
        await state.set_state(UserFlow.waiting_for_text_prompt)
        if result.effective_text_role:
            await state.update_data(text_role=result.effective_text_role)

    await _reply_chat_turn_result(
        message,
        result,
        stream_cb=stream_cb,
        table_context=table_context,
    )
