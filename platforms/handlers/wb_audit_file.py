"""Перехватчик .xlsx / .csv в FSM аудита Wildberries (шаг 2 — после выбора налога)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from content import messages as msg
from platforms.handlers import deps
from platforms.marketplace_audit_flow import audit_tax_preset_from_data, dismiss_fsm_chat_message
from platforms.telegram_flood_safe import flood_safe_chat_action_loop
from platforms.telegram_states import WBAuditingStates
from services.file_processor import (
    DocumentTooBigError,
    download_telegram_document_to_path,
    is_spreadsheet_suffix,
)
from services.table_processing_worker import run_table_processing_worker_async
from services.table_subrole_types import normalize_table_subrole
from services.table_xlsx_flow import marketplace_requires_local_path, run_xlsx_fast_path_turn

router = Router()
logger = logging.getLogger(__name__)


def _document_suffix(file_name: str | None) -> str:
    return Path((file_name or "document").strip()).suffix.lower()


@router.message(WBAuditingStates.wait_for_xlsx, F.document)
async def wb_audit_file_process(message: Message, state: FSMContext) -> None:
    """Асинхронный перехватчик финансового отчёта Wildberries."""
    from platforms.neurotext_input import (
        _fail_wb_finance_status,
        _reply_chat_turn_result,
        _send_wb_finance_processing_status,
        _table_xlsx_allowed,
    )

    doc = message.document
    if doc is None:
        return

    file_name = (doc.file_name or "document").strip()
    suffix = _document_suffix(file_name)
    if not is_spreadsheet_suffix(suffix) or suffix not in {".xlsx", ".csv"}:
        await message.answer(msg.TXT_AUDIT_WB_FILE_BAD_FORMAT, parse_mode=ParseMode.HTML)
        return

    uid = message.from_user.id
    if not await _table_xlsx_allowed(uid):
        await message.answer(msg.TXT_AUDIT_WB_TARIFF_BLOCKED, parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    table_subrole = normalize_table_subrole(data.get("table_subrole"))
    audit_platform = data.get("audit_platform") or "wildberries"
    tax_preset_id = audit_tax_preset_from_data(data).id

    status_msg = await _send_wb_finance_processing_status(message)
    temp_path: str | None = None

    try:
        temp_path = await asyncio.wait_for(
            download_telegram_document_to_path(deps.bot(), doc, file_name=file_name),
            timeout=120.0,
        )

        await dismiss_fsm_chat_message(
            state,
            chat_id=message.chat.id,
            data_key="audit_upload_prompt_message_id",
        )
        await dismiss_fsm_chat_message(
            state,
            chat_id=message.chat.id,
            data_key="instruction_msg_id",
        )

        is_csv = suffix == ".csv"
        title = Path(file_name).stem or "Отчёт NeuroMule"
        worker = await run_table_processing_worker_async(
            temp_path,
            table_subrole,
            is_csv,
            title=title,
            marketplace_platform=str(audit_platform),
        )
        if worker is None or not worker.rows:
            await _fail_wb_finance_status(
                message,
                status_msg,
                msg.TXT_AUDIT_WB_DIGITIZE_FAILED,
            )
            return

        _, preparse = marketplace_requires_local_path(worker.rows, title=title)
        from services.file_processor import should_warn_column_structure

        column_structure_warning = should_warn_column_structure(
            worker.rows,
            revenue_total=float(preparse.revenue_total or 0.0),
        )

        async with flood_safe_chat_action_loop(deps.bot(), message.chat.id, "typing"):
            fast_result = await run_xlsx_fast_path_turn(
                settings,
                uid,
                worker.rows,
                file_name=file_name,
                title=title,
                table_subrole=table_subrole,
                prebuilt_worker=worker,
                column_structure_warning=column_structure_warning,
                marketplace_platform=str(audit_platform),
                source_file_path=temp_path,
                tax_preset_id=tax_preset_id,
            )

        await _reply_chat_turn_result(
            message,
            fast_result,
            stream_cb=None,
            table_context=f"[📊 Excel {file_name}]",
            prefer_table_error=True,
            status_message=status_msg,
            table_subrole=table_subrole,
            audit_platform=str(audit_platform),
        )
    except DocumentTooBigError:
        await message.answer(msg.TXT_AUDIT_WB_FILE_TOO_BIG, parse_mode=ParseMode.HTML)
    except asyncio.TimeoutError:
        await message.answer(
            "⚠️ Не удалось скачать файл с серверов Telegram. Попробуйте ещё раз.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("wb_audit_file_process failed uid=%s file=%s", uid, file_name)
        await _fail_wb_finance_status(
            message,
            status_msg,
            msg.TXT_AUDIT_WB_DIGITIZE_FAILED,
        )
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
        # state.clear() намеренно не вызываем — селлер может загружать файлы пачкой
