"""Обработка входящих сообщений Нейротекста (текст / фото / документ)."""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from pathlib import Path

from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from content import messages as msg
from platforms.handlers import deps
from platforms.neurotext_flow import ensure_neurotext_waiting_state, send_neurotext_role_menu
from platforms.telegram_flood_safe import flood_safe_answer, flood_safe_chat_action_loop
from platforms.telegram_streaming import (
    TELEGRAM_STREAM_EDIT_INTERVAL_SEC,
    create_telegram_streaming_handle,
)
from services.bot_activity_indicator import bot_typing_indicator
from platforms.table_generator_delivery import send_table_generator_pack
from platforms.telegram_chunks import answer_chat_text, answer_free_standard_success
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
    document_too_big_message,
    download_telegram_document_to_path,
    is_spreadsheet_suffix,
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
from services.telegram_safe_text import _escape_telegram_html
from services.table_mass_seo_flow import run_mass_seo_xlsx_turn
from services.table_processing_worker import run_table_processing_worker_async
from services.table_subrole_types import DEFAULT_TABLE_SUBROLE, normalize_table_subrole
from services.table_xlsx_flow import (
    build_xlsx_api_user_prompt,
    marketplace_requires_local_path,
    run_xlsx_fast_path_turn,
)
from services.table_json import parse_table_json_response, table_payload_has_data
from services.use_cases.chat_turn import ChatTurnOutcome, ChatTurnResult, run_chat_turn

logger = logging.getLogger(__name__)

_BLOGGER_ROLE_IDS = frozenset({"blogger_content", "blogger"})


def _tariff_is_free(tariff: object | None) -> bool:
    from services.billing.types import TariffTier

    if tariff is TariffTier.FREE:
        return True
    if isinstance(tariff, str) and TariffTier.from_db(tariff) is TariffTier.FREE:
        return True
    return False


async def _user_is_free(user_id: int, *, tariff_hint: object | None = None) -> bool:
    if _tariff_is_free(tariff_hint):
        return True
    try:
        from services.billing.types import TariffTier
        from services.repository import get_user_row

        row = await get_user_row(user_id)
        return bool(row and TariffTier.from_db(row.tariff) is TariffTier.FREE)
    except Exception:
        logger.exception("suggested_replies: tariff lookup failed uid=%s", user_id)
        return False


def _root_user_prompt_from_result(result: ChatTurnResult) -> str:
    """Исходный текст пользователя текущего turn (не из истории диалога)."""
    return (getattr(result, "root_user_prompt", None) or "").strip()


def _bind_hint_session_after_send(action_uuid: str | None, sent) -> None:
    """После успешного send проставляет message_id в HintSession (L1; L2 — async)."""
    if not action_uuid or sent is None:
        return
    mid = getattr(sent, "message_id", None)
    if mid is None:
        return
    from services.standard_suggested_replies import bind_hint_session_message

    bind_hint_session_message(action_uuid, int(mid))


async def _bind_hint_session_after_send_persisted(action_uuid: str | None, sent) -> None:
    """L1 + L2 bind после send."""
    if not action_uuid or sent is None:
        return
    mid = getattr(sent, "message_id", None)
    if mid is None:
        return
    from services.standard_suggested_replies import bind_hint_session_message_persisted

    await bind_hint_session_message_persisted(action_uuid, int(mid))


async def _hint_session_keyboard(
    user_id: int,
    *,
    body: str,
    labels: list[str] | tuple[str, ...] | None,
    root_user_prompt: str,
    ensure_three: bool = False,
):
    """Шаг create+persist → build_hint_keyboard (message_id=None до send).

    Returns:
        ``(keyboard, action_uuid)`` или ``(None, None)``.
    """
    from services.standard_suggested_replies import (
        build_hint_keyboard,
        create_hint_session_persisted,
        ensure_free_hint_labels,
    )

    clean: list[str]
    if ensure_three:
        clean = ensure_free_hint_labels(labels, body=body or "")
    else:
        clean = [str(x) for x in (labels or ()) if str(x).strip()]
        if not clean:
            clean = ensure_free_hint_labels(body=body or "")
    if not clean:
        return None, None
    action_uuid = await create_hint_session_persisted(
        user_id,
        body=body or "",
        labels=clean,
        root_user_prompt=root_user_prompt or "",
        message_id=None,
    )
    kb = build_hint_keyboard(action_uuid, clean)
    if kb is None:
        return None, None
    return kb, action_uuid


async def _standard_suggested_reply_markup(user_id: int, result: ChatTurnResult):
    """Suggested Replies для роли standard → ``(keyboard, action_uuid|None)``.

    Новый путь: HintSession + ``btn:``. Legacy ``chat_hint`` / ``std_reply`` —
    только fallback, если session-клавиатура не собралась.
    """
    try:
        labels = list(getattr(result, "suggested_replies", ()) or ())
        role = (result.effective_text_role or "").strip().lower()
        body = getattr(result, "assistant_message", None) or ""
        root = _root_user_prompt_from_result(result)
        from services.repository import get_show_suggested_replies
        from services.standard_suggested_replies import (
            build_free_hint_keyboard,
            build_suggested_replies_keyboard,
            remember_suggested_replies,
        )

        is_free = await _user_is_free(
            user_id, tariff_hint=getattr(result, "tariff", None)
        )

        # FREE standard: кнопки всегда — pref/БД/пустые лейблы не имеют значения.
        if is_free and role in ("", "standard"):
            kb, action_uuid = await _hint_session_keyboard(
                user_id,
                body=body,
                labels=labels or None,
                root_user_prompt=root,
                ensure_three=True,
            )
            if kb is None:
                # Legacy soft-fallback: старые chat_hint кнопки, без HintSession.
                kb = build_free_hint_keyboard(labels or None, body=body)
                action_uuid = None
            logger.info(
                "suggested_replies: FREE keyboard ready uid=%s buttons=%s hint=%s",
                user_id,
                len(kb.inline_keyboard) if kb else 0,
                bool(action_uuid),
            )
            return kb, action_uuid

        if role != "standard":
            return None, None

        from services.copy_pack import suppress_suggested_replies_for_answer

        if suppress_suggested_replies_for_answer(body):
            logger.info("suggested_replies: skipped (copy pack) uid=%s", user_id)
            return None, None

        if not await get_show_suggested_replies(user_id):
            logger.info(
                "suggested_replies: skipped (pref off) uid=%s labels=%s",
                user_id,
                len(labels),
            )
            return None, None
        if not labels:
            from services.standard_suggested_replies import ensure_free_hint_labels

            labels = ensure_free_hint_labels(body=body)
            logger.info(
                "suggested_replies: paid fallback labels uid=%s n=%s",
                user_id,
                len(labels),
            )
        if not labels:
            return None, None

        kb, action_uuid = await _hint_session_keyboard(
            user_id,
            body=body,
            labels=labels,
            root_user_prompt=root,
            ensure_three=False,
        )
        if kb is not None:
            return kb, action_uuid

        # Legacy soft-fallback: std_reply + _CACHE (не HintSession).
        context_id = remember_suggested_replies(user_id, labels) or "x"
        return build_suggested_replies_keyboard(context_id, labels), None
    except Exception:
        # Ошибка БД/клавиатуры не должна глушить весь ответ пользователю.
        logger.exception("suggested_replies: markup failed uid=%s", user_id)
        try:
            from services.standard_suggested_replies import build_free_hint_keyboard

            if await _user_is_free(
                user_id, tariff_hint=getattr(result, "tariff", None)
            ):
                return (
                    build_free_hint_keyboard(
                        body=getattr(result, "assistant_message", None) or "",
                    ),
                    None,
                )
        except Exception:
            logger.exception(
                "suggested_replies: FREE emergency keyboard failed uid=%s", user_id
            )
        return None, None


async def _free_standard_fallback_keyboard(
    user_id: int,
    *,
    body: str | None = None,
    tariff: object | None = None,
    labels: list[str] | tuple[str, ...] | None = None,
):
    """Клавиатура из 3 кнопок для FREE (в т.ч. при AI_FAILED / если markup=None).

    Fail-path без полного HintSession: legacy ``chat_hint`` (кнопки всё равно есть).
    """
    from services.standard_suggested_replies import build_free_hint_keyboard

    try:
        if not await _user_is_free(user_id, tariff_hint=tariff):
            return None
        return build_free_hint_keyboard(labels, body=body or "")
    except Exception:
        logger.exception("suggested_replies: FREE fail keyboard uid=%s", user_id)
        try:
            if _tariff_is_free(tariff):
                return build_free_hint_keyboard(labels, body=body or "")
        except Exception:
            logger.exception(
                "suggested_replies: FREE emergency keyboard failed uid=%s", user_id
            )
        return None


def _iron_free_hint_keyboard(result: ChatTurnResult):
    """Синхронный last-resort: FREE SUCCESS без кнопок недопустим (legacy chat_hint)."""
    from services.standard_suggested_replies import (
        build_free_hint_keyboard,
        prepare_free_standard_reply,
    )

    if not _tariff_is_free(getattr(result, "tariff", None)):
        return None
    role = (result.effective_text_role or "standard").strip().lower()
    if role not in ("", "standard"):
        return None
    try:
        labels = list(getattr(result, "suggested_replies", ()) or ())
        body = getattr(result, "assistant_message", None) or ""
        if labels:
            return build_free_hint_keyboard(labels, body=body)
        _body, _labels, kb = prepare_free_standard_reply(body)
        return kb
    except Exception:
        logger.exception("suggested_replies: iron FREE keyboard failed")
        return build_free_hint_keyboard()


async def _success_reply_keyboard(owner_id: int, result: ChatTurnResult):
    """Клавиатура под SUCCESS-ответом: blogger → HintSession → FREE iron.

    Returns:
        ``(reply_kb, blogger_post_id, action_uuid)``.
        ``action_uuid`` заполнен только для stateful ``btn:`` клавиатуры.
    """
    from services.standard_suggested_replies import prepare_free_standard_reply

    blogger_kb = None
    blogger_post_id: str | None = None
    action_uuid: str | None = None
    if (result.effective_text_role or "") in _BLOGGER_ROLE_IDS:
        blogger_kb, blogger_post_id = await _blogger_reply_markup(
            owner_id,
            result.assistant_message or "",
            blogger_post_raw=result.blogger_post_raw,
        )
    if blogger_kb is not None:
        return blogger_kb, blogger_post_id, None

    reply_kb, action_uuid = await _standard_suggested_reply_markup(owner_id, result)
    if reply_kb is None:
        reply_kb = await _free_standard_fallback_keyboard(
            owner_id,
            body=result.assistant_message,
            tariff=getattr(result, "tariff", None),
            labels=list(getattr(result, "suggested_replies", ()) or ()) or None,
        )
        action_uuid = None
    if reply_kb is None:
        reply_kb = _iron_free_hint_keyboard(result)
        action_uuid = None

    # Финальный FORCE: FREE + standard → кнопки любой ценой (БД или tariff на result).
    if reply_kb is None and await _user_is_free(
        owner_id, tariff_hint=getattr(result, "tariff", None)
    ):
        role = (result.effective_text_role or "standard").strip().lower()
        if role in ("", "standard"):
            try:
                _b, _l, reply_kb = prepare_free_standard_reply(
                    result.assistant_message or ""
                )
            except Exception:
                from services.standard_suggested_replies import build_free_hint_keyboard

                reply_kb = build_free_hint_keyboard()
            action_uuid = None
            logger.warning(
                "suggested_replies: FORCE free keyboard uid=%s buttons=%s",
                owner_id,
                len(reply_kb.inline_keyboard) if reply_kb else 0,
            )
    return reply_kb, blogger_post_id, action_uuid


async def _blogger_reply_markup(user_id: int, assistant_message: str, *, blogger_post_raw: str | None = None):
    """Кэширует черновик поста и возвращает (клавиатуру, post_id)."""
    from content.inline_keyboards import get_blogger_keyboard
    from services import blogger_post_cache
    from services.blogger_post_parser import canonicalize_blogger_cache_raw

    if (blogger_post_raw or "").strip():
        raw_for_cache = canonicalize_blogger_cache_raw(blogger_post_raw)
    else:
        logger.warning(
            "blogger_post_raw missing uid=%s — fallback to display text (обложка может не сработать)",
            user_id,
        )
        raw_for_cache = canonicalize_blogger_cache_raw(assistant_message)

    post_id = await blogger_post_cache.remember_async(user_id, raw_for_cache)
    return get_blogger_keyboard(post_id), post_id


_ZERO_WIDTH_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff", "\xa0")


def _normalize_document_caption(message: Message) -> str:
    """Подпись к документу: None → '', без zero-width и лишних пробелов."""
    raw = message.caption if message.caption is not None else ""
    for ch in _ZERO_WIDTH_CHARS:
        raw = raw.replace(ch, "")
    return raw.strip()


def _document_suffix(file_name: str | None) -> str:
    return Path((file_name or "document").strip()).suffix.lower()


async def _table_xlsx_allowed(user_id: int) -> bool:
    if billing_bypass(user_id):
        return True
    row = await get_user_row(user_id)
    return TariffTier.from_db(row.tariff) is not TariffTier.FREE


async def _answer_local_pipeline_traceback(
    message: Message,
    *,
    fallback: str | None = None,
) -> None:
    """Временная отладка: реальный traceback вместо заглушки AI_UNAVAILABLE."""
    trace = traceback.format_exc()
    if not trace or trace.strip() in ("NoneType: None", "None"):
        body = _escape_telegram_html(
            fallback or "Исключение не зафиксировано (sys.exc_info пуст)."
        )
    else:
        body = _escape_telegram_html(trace)
    if len(body) > 3800:
        body = body[:3800] + "…"
    await message.answer(
        f"❌ <b>Критический сбой локального пайплайна:</b>\n<pre>{body}</pre>",
        parse_mode=ParseMode.HTML,
    )


async def _send_table_generator_status(message: Message) -> Message:
    """Статус ожидания для роли table_generator (без стриминга чанками)."""
    return await flood_safe_answer(
        message,
        msg.TXT_TABLE_GENERATOR_STATUS,
        parse_mode=ParseMode.HTML,
    )


async def _send_wb_finance_processing_status(message: Message) -> Message:
    """Статус WB-аудита: один раз «Оцифровываю…» без последующих edit_text до выдачи отчёта."""
    return await flood_safe_answer(
        message,
        "⏳ <b>Оцифровываю финансовый отчёт…</b>",
        parse_mode=ParseMode.HTML,
    )


async def _fail_wb_finance_status(
    message: Message,
    status_msg: Message | None,
    text: str,
) -> None:
    """Ошибка WB-аудита: удаляем статус «Оцифровываю…», новое сообщение без edit_text."""
    await _clear_table_status_on_failure(status_msg)
    await message.answer(text, parse_mode=ParseMode.HTML)


async def _notify_table_status(
    message: Message,
    status_msg: Message | None,
    text: str,
) -> None:
    """Редактирует статус или шлёт новое сообщение, если статуса не было."""
    if status_msg is not None:
        try:
            await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
            return
        except Exception:
            logger.debug("status_msg.edit_text failed", exc_info=True)
    await message.answer(text, parse_mode=ParseMode.HTML)


def _is_table_spreadsheet_document(suffix: str, role_id: str) -> bool:
    """Табличный пайплайн только в роли table_generator (ИИ-Аналитик Excel)."""
    return is_spreadsheet_suffix(suffix) and role_id == "table_generator"


async def _reply_spreadsheet_requires_analyst_role(
    message: Message,
    status_msg: Message | None,
) -> None:
    text = msg.TXT_SPREADSHEET_REQUIRES_ANALYST_ROLE
    if status_msg is not None:
        try:
            await status_msg.edit_text(text, parse_mode=ParseMode.HTML)
            return
        except Exception:
            pass
    await message.answer(text, parse_mode=ParseMode.HTML)


def _use_table_api_path(caption: str, table_subrole: str) -> bool:
    """OpenRouter API-path только для стандартного отчёта с подписью к файлу."""
    return bool(caption.strip()) and normalize_table_subrole(table_subrole) == "standard_report"


def _table_ai_result_needs_degradation(result) -> bool:
    if result.outcome in (ChatTurnOutcome.AI_FAILED, ChatTurnOutcome.TABLE_JSON_INVALID):
        return True
    if result.outcome is ChatTurnOutcome.SUCCESS and result.table_raw_json:
        try:
            payload = parse_table_json_response(result.table_raw_json)
            if payload is None or not table_payload_has_data(payload):
                return True
        except Exception:
            return True
    return False


async def _apply_table_graceful_degradation(
    user_id: int,
    result,
    *,
    fallback_rows: list[list[str]] | None,
    fallback_worker: object | None,
    file_name: str,
    title: str,
    table_subrole: str | None,
    column_structure_warning: bool = False,
):
    """Refund уже выполнен в chat_turn → локальный fast-path без повторного списания."""
    if not _table_ai_result_needs_degradation(result):
        return result
    if not fallback_rows:
        return result
    try:
        return await run_xlsx_fast_path_turn(
            settings,
            user_id,
            fallback_rows,
            file_name=file_name,
            title=title,
            table_subrole=table_subrole,
            prebuilt_worker=fallback_worker,
            skip_billing=True,
            column_structure_warning=column_structure_warning,
        )
    except Exception:
        logger.exception("table graceful degradation failed uid=%s", user_id)
        return result


async def _clear_table_status_on_failure(status_message: Message | None) -> None:
    if status_message is None:
        return
    try:
        await status_message.delete()
    except Exception:
        logger.debug("table status cleanup on failure failed", exc_info=True)


def _chat_ai_failure_text(result) -> str:
    """Понятное сообщение при сбое OpenRouter (не generic «подкова» для медиа-очереди)."""
    if result.user_notice:
        return result.user_notice
    role = (result.effective_text_role or "").strip().lower()
    if role in _BLOGGER_ROLE_IDS:
        return msg.TXT_BLOGGER_AI_FAILED
    return msg.TXT_CHAT_AI_UNAVAILABLE


async def _delete_chat_busy_notice(
    message: Message,
    user_id: int,
    *,
    message_id: int | None = None,
) -> None:
    """Удаляет «⏳ ещё отвечаю» после успешного ответа ИИ."""
    mid = message_id
    if mid is None:
        from services.rate_limit_service import pop_chat_busy_message_id

        mid = await pop_chat_busy_message_id(settings, user_id)
    if mid is None:
        return
    try:
        # В личке chat_id == user_id, в группах — message.chat.id.
        await message.bot.delete_message(chat_id=message.chat.id, message_id=int(mid))
    except Exception:
        # Уже удалено пользователем / слишком старое — игнорируем.
        logger.debug(
            "chat_busy notice delete failed uid=%s mid=%s",
            user_id,
            mid,
            exc_info=True,
        )


async def _reply_chat_turn_result(
    message: Message,
    result,
    *,
    stream_handle,
    table_context: str = "",
    prefer_table_error: bool = False,
    status_message: Message | None = None,
    table_subrole: str | None = None,
    audit_platform: str | None = None,
    reply_user_id: int | None = None,
) -> None:
    owner_id = int(
        reply_user_id
        if reply_user_id is not None
        else (message.from_user.id if message.from_user else 0)
    )
    if result.outcome is ChatTurnOutcome.SUCCESS:
        await _delete_chat_busy_notice(
            message,
            owner_id,
            message_id=getattr(result, "busy_notice_message_id", None),
        )
        if result.user_notice:
            await message.answer(
                result.user_notice,
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        if stream_handle is not None and result.assistant_message:
            role = (result.effective_text_role or "standard").strip().lower()
            is_free = await _user_is_free(
                owner_id, tariff_hint=getattr(result, "tariff", None)
            )
            # FREE: стрим отключаем — только атомарный send с кнопками.
            if is_free and role in ("", "standard"):
                await answer_free_standard_success(
                    message,
                    result.assistant_message,
                    settings,
                    user_id=owner_id,
                    labels=list(getattr(result, "suggested_replies", ()) or ())
                    or None,
                    root_user_prompt=_root_user_prompt_from_result(result),
                )
            else:
                reply_kb, blogger_post_id, action_uuid = await _success_reply_keyboard(
                    owner_id, result
                )

                async def _on_finalized(sent_message: Message) -> None:
                    await _bind_hint_session_after_send_persisted(
                        action_uuid, sent_message
                    )
                    if not blogger_post_id:
                        return
                    from services import blogger_post_cache

                    await blogger_post_cache.bind_telegram_message(
                        blogger_post_id,
                        owner_id,
                        chat_id=sent_message.chat.id,
                        message_id=sent_message.message_id,
                    )

                try:
                    await stream_handle.finalize(
                        result.assistant_message,
                        reply_markup=reply_kb,
                        on_finalized=_on_finalized
                        if (action_uuid or blogger_post_id)
                        else None,
                    )
                except Exception:
                    logger.exception(
                        "stream_handle.finalize failed uid=%s — fallback answer",
                        owner_id,
                    )
                    reply_kb, _, _ = await _success_reply_keyboard(owner_id, result)
                    await message.answer(
                        result.assistant_message or msg.TXT_CHAT_AI_UNAVAILABLE,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_kb,
                    )
        elif stream_handle is None:
            if result.table_raw_json:
                try:
                    delivered = await send_table_generator_pack(
                        message,
                        result.table_raw_json,
                        context_text=table_context,
                        report_id=result.table_report_id,
                        status_message=status_message,
                        ai_insights=result.table_ai_insights,
                        table_subrole=table_subrole,
                        table_worker=result.table_worker,
                        seo_xlsx_bytes=result.table_seo_xlsx_bytes,
                        degradation_notice=result.table_degradation_notice,
                        audit_platform=audit_platform,
                    )
                except Exception:
                    logger.exception(
                        "send_table_generator_pack failed uid=%s",
                        message.from_user.id,
                    )
                    await _clear_table_status_on_failure(status_message)
                    await _answer_local_pipeline_traceback(message)
                    return
                if not delivered:
                    await _clear_table_status_on_failure(status_message)
                    await message.answer(
                        msg.TXT_TABLE_AI_FAILED_NO_ROWS,
                        parse_mode=ParseMode.HTML,
                    )
            elif result.assistant_message:
                if table_context or table_subrole:
                    await message.answer(
                        msg.TXT_TABLE_AI_FAILED_NO_ROWS,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    role = (result.effective_text_role or "standard").strip().lower()
                    is_free = await _user_is_free(
                        owner_id, tariff_hint=getattr(result, "tariff", None)
                    )
                    # FREE Standard: ОДНА атомарная отправка (текст+кнопки вместе).
                    if is_free and role in ("", "standard"):
                        await answer_free_standard_success(
                            message,
                            result.assistant_message,
                            settings,
                            user_id=owner_id,
                            labels=list(getattr(result, "suggested_replies", ()) or ())
                            or None,
                            root_user_prompt=_root_user_prompt_from_result(result),
                        )
                    else:
                        reply_kb, _blogger_post_id, action_uuid = (
                            await _success_reply_keyboard(owner_id, result)
                        )
                        sent = await answer_chat_text(
                            message,
                            result.assistant_message,
                            settings,
                            reply_markup=reply_kb,
                        )
                        await _bind_hint_session_after_send_persisted(action_uuid, sent)
            else:
                # SUCCESS без текста — не молчим (раньше был silent return).
                fail_kb = await _free_standard_fallback_keyboard(owner_id)
                await message.answer(
                    msg.TXT_CHAT_AI_UNAVAILABLE,
                    parse_mode=ParseMode.HTML,
                    reply_markup=fail_kb,
                )
        elif not result.user_notice and not result.table_raw_json:
            fail_kb = await _free_standard_fallback_keyboard(owner_id)
            await message.answer(
                msg.TXT_CHAT_AI_UNAVAILABLE,
                parse_mode=ParseMode.HTML,
                reply_markup=fail_kb,
            )
        return
    await _clear_table_status_on_failure(status_message)
    if result.outcome is ChatTurnOutcome.EMPTY_INPUT:
        await message.answer(msg.TXT_CHAT_EMPTY)
        return
    if result.outcome is ChatTurnOutcome.CONTEXT_TOO_LARGE:
        await message.answer(msg.TXT_CHAT_CONTEXT_TOO_LARGE)
        return
    if result.outcome is ChatTurnOutcome.RATE_LIMITED:
        await message.answer(msg.TXT_CHAT_RATE_LIMIT)
        return
    if result.outcome is ChatTurnOutcome.CHAT_BUSY:
        notice = (result.user_notice or msg.TXT_CHAT_BUSY).strip()
        from services.rate_limit_service import remember_chat_busy_message_id

        sent = await message.answer(notice)
        if sent is not None and getattr(sent, "message_id", None) is not None:
            await remember_chat_busy_message_id(
                settings, owner_id, int(sent.message_id)
            )
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
    if result.outcome is ChatTurnOutcome.DAILY_LIMIT_EXCEEDED:
        await message.answer(
            result.user_notice or msg.TXT_CHAT_DAILY_LIMIT,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    if result.outcome in (ChatTurnOutcome.AI_FAILED, ChatTurnOutcome.TABLE_JSON_INVALID):
        fail_kb = None
        if not (table_context or table_subrole or prefer_table_error):
            fail_kb = await _free_standard_fallback_keyboard(owner_id)
        if result.user_notice:
            await _clear_table_status_on_failure(status_message)
            notice_kb = fail_kb
            if result.user_notice == msg.TXT_CHAT_AI_MAINTENANCE:
                notice_kb = paycat.shop_packages_keyboard()
            await message.answer(
                result.user_notice,
                parse_mode=ParseMode.HTML,
                reply_markup=notice_kb,
            )
            return
        if table_context or table_subrole or prefer_table_error:
            await message.answer(
                msg.TXT_TABLE_AI_FAILED_NO_ROWS,
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                _chat_ai_failure_text(result),
                parse_mode=ParseMode.HTML,
                reply_markup=fail_kb,
            )
        return
    fail_kb = await _free_standard_fallback_keyboard(owner_id)
    await message.answer(
        _chat_ai_failure_text(result),
        parse_mode=ParseMode.HTML,
        reply_markup=fail_kb,
    )


async def handle_neurotext_user_message(
    message: Message,
    state: FSMContext,
    *,
    keep_waiting_state: bool = True,
    forced_user_text: str | None = None,
    forced_user_id: int | None = None,
    anchor_assistant_text: str | None = None,
) -> None:
    """Единая точка: текст, фото или документ → ``run_chat_turn``.

    ``forced_user_text`` / ``forced_user_id`` — Suggested Reply
    (``chat_hint:`` / legacy ``std_reply:``): текст кнопки и uid пользователя
    (у ``callback.message.from_user`` — бот).

    ``anchor_assistant_text`` — текст сообщения бота, под которым нажали кнопку
    (чтобы follow-up не цеплялся к более новому ответу в истории).
    """
    # Nav Reply-кнопки никогда не должны уходить в OpenRouter «typing».
    if forced_user_text is None and (message.text or "").strip():
        from platforms.telegram_utils import (
            is_image_reply_button_text,
            is_reply_nav_button_text,
        )

        raw_nav = (message.text or "").strip()
        if is_image_reply_button_text(raw_nav):
            from platforms.image_menu_flow import present_image_model_menu

            await present_image_model_menu(message, state, message.from_user.id)
            return
        if is_reply_nav_button_text(raw_nav):
            from platforms.telegram_utils import try_dispatch_reply_nav_button

            if await try_dispatch_reply_nav_button(message, state):
                return
            await message.answer(
                "⚠️ Не удалось открыть меню. Нажмите /start.",
                parse_mode=ParseMode.HTML,
            )
            return

    if forced_user_text is None and (message.text or "").strip():
        from platforms.image_menu_flow import (
            can_intercept_text_as_image_prompt,
            handle_pending_image_menu_text,
        )

        if await can_intercept_text_as_image_prompt(message, state):
            await handle_pending_image_menu_text(message, state)
            return

    is_photo = bool(message.photo) and forced_user_text is None
    is_document = bool(message.document) and forced_user_text is None

    if not is_photo and not is_document and forced_user_text is None:
        from platforms.build_info import reply_build_version, slash_command_base

        cmd = slash_command_base(message.text)
        if cmd:
            if cmd == "/version":
                await reply_build_version(message)
            else:
                await message.answer(
                    "⚠️ Команда недоступна в этом режиме. Нажмите /start.",
                    parse_mode=ParseMode.HTML,
                )
            return

    xlsx_auto_finance = False

    data_early = await state.get_data()
    role_id_early = str(data_early.get("text_role") or "standard").strip().lower()

    if (
        forced_user_text is None
        and not is_photo
        and not is_document
        and not has_neurotext_message_input(message)
    ):
        if role_id_early == "summary":
            from platforms.summarizer_flow import send_summary_mode_hint

            await send_summary_mode_hint(message)
        else:
            await send_neurotext_role_menu(message, state)
        return

    # Финансовый аудит площадки — только после выбора WB/Ozon/…, не в любой роли Нейротекста.
    if is_document and _document_suffix(message.document.file_name) in (".xlsx", ".csv"):
        from platforms.marketplace_audit_flow import is_marketplace_audit_context

        current_state = await state.get_state()
        data_pre = await state.get_data()
        if is_marketplace_audit_context(current_state, data_pre):
            await state.update_data(
                text_role="table_generator",
                table_subrole="wb_ozon_finance",
            )
            xlsx_auto_finance = True

    # WB: без выбора налога файл не принимаем.
    if is_document and _document_suffix(message.document.file_name) in (".xlsx", ".csv"):
        from platforms.marketplace_audit_flow import is_audit_tax_waiting_state

        if is_audit_tax_waiting_state(await state.get_state()):
            await message.answer(msg.TXT_AUDIT_WB_TAX_REQUIRED, parse_mode=ParseMode.HTML)
            return

    await ensure_neurotext_waiting_state(state)
    data = await state.get_data()
    role_id = str(data.get("text_role") or "standard").strip().lower()

    if role_id == "summary":
        from platforms.summarizer_flow import handle_summary_neurotext_message

        await handle_summary_neurotext_message(
            message,
            state,
            keep_waiting_state=keep_waiting_state,
        )
        return

    table_subrole = normalize_table_subrole(data.get("table_subrole"))
    audit_platform = data.get("audit_platform")
    from platforms.marketplace_audit_flow import audit_tax_preset_from_data

    audit_tax_preset = audit_tax_preset_from_data(data).id
    if forced_user_id is not None:
        uid = int(forced_user_id)
    elif message.from_user is not None:
        uid = message.from_user.id
    else:
        return

    user_image_data_url: str | None = None
    xlsx_fallback_prompt: str | None = None
    xlsx_fallback_rows: list[list[str]] | None = None
    table_fallback_worker: object | None = None
    table_fallback_title: str = "Отчёт NeuroMule"
    source_file_name: str = "report.xlsx"
    is_xlsx_api_path = False
    column_structure_warning = False
    status_msg: Message | None = None
    is_wb_finance_subrole = False
    try:
        if is_document:
            doc = message.document
            file_name = (doc.file_name or "document").strip()
            source_file_name = file_name
            suffix = _document_suffix(file_name)
            caption = _normalize_document_caption(message)
            logger.warning(
                "document received role=%s, suffix=%s, caption=%r, file=%s, uid=%s",
                role_id,
                suffix,
                caption,
                file_name,
                uid,
            )
            if is_spreadsheet_suffix(suffix) and role_id != "table_generator":
                await _reply_spreadsheet_requires_analyst_role(message, None)
                return

            is_table_xlsx = _is_table_spreadsheet_document(suffix, role_id)
            is_wb_finance_subrole = (
                normalize_table_subrole(table_subrole) == "wb_ozon_finance"
            )
            if is_table_xlsx and not is_wb_finance_subrole:
                status_msg = await _send_table_generator_status(message)
            elif is_table_xlsx:
                status_msg = await _send_wb_finance_processing_status(message)
            else:
                status_msg = await message.answer(
                    "📎 <b>Читаю документ…</b>",
                    parse_mode=ParseMode.HTML,
                )

            if is_table_xlsx:
                from platforms.marketplace_audit_flow import (
                    dismiss_fsm_chat_message,
                    is_marketplace_audit_context,
                )

                if is_marketplace_audit_context(await state.get_state(), await state.get_data()):
                    await dismiss_fsm_chat_message(
                        state,
                        chat_id=message.chat.id,
                    )
                    await dismiss_fsm_chat_message(
                        state,
                        chat_id=message.chat.id,
                        data_key="instruction_msg_id",
                    )
                if not await _table_xlsx_allowed(uid):
                    deny_text = (
                        f"⛔ {msg.TXT_AI_ANALYST_ROLE_PHRASE} доступна с тарифа <b>MINI</b> и выше. "
                        "Открой «🚀 Тарифы» для подключения."
                    )
                    if is_wb_finance_subrole:
                        await _fail_wb_finance_status(message, status_msg, deny_text)
                    else:
                        await _notify_table_status(message, status_msg, deny_text)
                    return
                file_path = await asyncio.wait_for(
                    download_telegram_document_to_path(deps.bot(), doc),
                    timeout=120.0,
                )
                is_csv = suffix == ".csv"
                title = Path(file_name).stem or "Отчёт NeuroMule"
                if is_wb_finance_subrole or xlsx_auto_finance:
                    from services.file_processor import check_wb_finance_upload_file
                    from services.table_text_response import (
                        is_wb_finance_invalid_structure,
                        wb_finance_invalid_structure_user_html,
                    )

                    structure_probe = await asyncio.to_thread(
                        check_wb_finance_upload_file,
                        file_path,
                    )
                    if is_wb_finance_invalid_structure(structure_probe):
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                        await _fail_wb_finance_status(
                            message,
                            status_msg,
                            wb_finance_invalid_structure_user_html(),
                        )
                        return
                xlsx_source_path: str | None = None
                try:
                    worker = await run_table_processing_worker_async(
                        file_path,
                        table_subrole,
                        is_csv,
                        title=title,
                        marketplace_platform=audit_platform,
                    )
                    if worker is not None and worker.rows:
                        from services.db_reports import save_user_report_to_db
                        from services.file_processor import build_report_metrics_for_history

                        report_pack = build_report_metrics_for_history(
                            worker.rows,
                            revenue_total=float(worker.calculated_total or 0),
                            platform=audit_platform or "wildberries",
                            tax_preset_id=str(audit_tax_preset)
                            if audit_tax_preset
                            else None,
                        )
                        await save_user_report_to_db(uid, report_pack)
                    if is_wb_finance_subrole or xlsx_auto_finance:
                        xlsx_source_path = file_path
                finally:
                    if xlsx_source_path is None and os.path.isfile(file_path):
                        os.remove(file_path)
                if worker is None or not worker.rows:
                    empty_text = "⚠️ Файл пустой или не содержит данных."
                    if is_wb_finance_subrole:
                        await _fail_wb_finance_status(message, status_msg, empty_text)
                    else:
                        await _notify_table_status(message, status_msg, empty_text)
                    return
                rows = worker.rows
                xlsx_fallback_rows = rows
                table_fallback_worker = worker
                table_fallback_title = title

                force_local_parse, preparse = marketplace_requires_local_path(rows, title=title)
                if force_local_parse:
                    use_local = True
                    from services.file_processor import should_warn_column_structure

                    column_structure_warning = should_warn_column_structure(
                        rows,
                        revenue_total=float(preparse.revenue_total or 0.0),
                    )
                else:
                    use_local = xlsx_auto_finance or not _use_table_api_path(
                        caption, table_subrole
                    )
                if table_subrole == "mass_seo_generation":
                    try:
                        async with flood_safe_chat_action_loop(
                            deps.bot(), message.chat.id, "typing"
                        ):
                            fast_result = await run_mass_seo_xlsx_turn(
                                settings,
                                uid,
                                rows,
                                file_name=file_name,
                                title=title,
                            )
                        if keep_waiting_state and fast_result.outcome is ChatTurnOutcome.SUCCESS:
                            await state.set_state(UserFlow.waiting_for_text_prompt)
                            if fast_result.effective_text_role:
                                await state.update_data(text_role=fast_result.effective_text_role)
                        await _reply_chat_turn_result(
                            message,
                            fast_result,
                            stream_handle=None,
                            table_context=f"[📝 SEO {file_name}]",
                            prefer_table_error=True,
                            status_message=status_msg,
                            table_subrole=table_subrole,
                        )
                    except Exception:
                        logger.exception("mass seo path failed uid=%s", uid)
                        await _clear_table_status_on_failure(status_msg)
                        await _answer_local_pipeline_traceback(message)
                    return

                if use_local:
                    try:
                        async with flood_safe_chat_action_loop(
                            deps.bot(), message.chat.id, "typing"
                        ):
                            fast_result = await run_xlsx_fast_path_turn(
                                settings,
                                uid,
                                rows,
                                file_name=file_name,
                                title=title,
                                table_subrole=table_subrole,
                                prebuilt_worker=worker,
                                column_structure_warning=column_structure_warning,
                                marketplace_platform=audit_platform,
                                source_file_path=xlsx_source_path,
                                tax_preset_id=str(audit_tax_preset) if audit_tax_preset else None,
                            )
                        if keep_waiting_state and fast_result.outcome is ChatTurnOutcome.SUCCESS:
                            from platforms.marketplace_audit_flow import is_marketplace_audit_context

                            if not is_marketplace_audit_context(
                                await state.get_state(),
                                await state.get_data(),
                            ):
                                await state.set_state(UserFlow.waiting_for_text_prompt)
                            if fast_result.effective_text_role:
                                await state.update_data(text_role=fast_result.effective_text_role)
                        await _reply_chat_turn_result(
                            message,
                            fast_result,
                            stream_handle=None,
                            table_context=f"[📊 Excel {file_name}]",
                            prefer_table_error=True,
                            status_message=status_msg,
                            table_subrole=table_subrole,
                            audit_platform=audit_platform,
                        )
                    except Exception:
                        logger.exception("xlsx fast-path failed uid=%s", uid)
                        await _clear_table_status_on_failure(status_msg)
                        await _answer_local_pipeline_traceback(message)
                    finally:
                        if xlsx_source_path is not None and os.path.isfile(xlsx_source_path):
                            os.remove(xlsx_source_path)
                    return
                raw_user_text = build_xlsx_api_user_prompt(
                    caption,
                    rows,
                    title=title,
                )
                dialog_text = caption or f"[📊 Excel {file_name}]"
                xlsx_fallback_prompt = raw_user_text
                xlsx_fallback_rows = rows
                is_xlsx_api_path = True
            elif suffix in NEUROTEXT_DOCUMENT_SUFFIXES:
                doc_payload = await telegram_document_to_neurotext_payload(
                    deps.bot(),
                    doc,
                    max_chars=settings.chat_max_message_chars,
                )
                if doc_payload.needs_vision:
                    try:
                        await status_msg.edit_text(
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
                        await status_msg.edit_text(msg.TXT_CHAT_EMPTY)
                        return
                    dialog_text = caption or f"[📄 {file_name}]"
            else:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                if suffix in (".xlsx", ".csv"):
                    await _reply_spreadsheet_requires_analyst_role(message, status_msg)
                else:
                    await message.answer(
                        "⚠️ Поддерживаются документы "
                        "<b>.txt</b>, <b>.csv</b>, <b>.pdf</b>, <b>.docx</b> "
                        "и <b>.xlsx</b> (только в <b>{}</b>).".format(
                            msg.TXT_AI_ANALYST_ROLE_PHRASE,
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                return
            if not is_table_xlsx:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
        elif is_photo:
            caption = _normalize_document_caption(message)
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
            if forced_user_text is not None:
                raw_user_text = forced_user_text.strip()
                dialog_text = None
            else:
                quoted_text, user_text = resolve_neurotext_quote_input(message)
                raw_user_text = build_quoted_user_prompt(user_text, quoted_text)
                dialog_text = (
                    user_text[: settings.chat_max_message_chars] if quoted_text else None
                )
    except asyncio.TimeoutError:
        logger.warning("telegram document download timeout uid=%s", uid)
        timeout_text = (
            "⚠️ <b>Не удалось скачать файл</b> (таймаут). "
            "Попробуйте ещё раз или отправьте файл без подписи."
        )
        if is_wb_finance_subrole:
            await _fail_wb_finance_status(message, status_msg, timeout_text)
        elif status_msg is not None:
            try:
                await status_msg.edit_text(timeout_text, parse_mode=ParseMode.HTML)
            except Exception:
                await message.answer(
                    "⚠️ Не удалось скачать файл с серверов Telegram. Попробуйте ещё раз.",
                    parse_mode=ParseMode.HTML,
                )
        else:
            await message.answer(
                "⚠️ Не удалось скачать файл с серверов Telegram. Попробуйте ещё раз.",
                parse_mode=ParseMode.HTML,
            )
        return
    except DocumentTooBigError as exc:
        file_name = message.document.file_name if message.document else None
        await message.answer(
            document_too_big_message(file_name or getattr(exc, "file_name", None)),
            parse_mode=ParseMode.HTML,
        )
        return
    except NeurotextUnsupportedDocumentError:
        await message.answer(
            "⚠️ Поддерживаются документы "
            "<b>.txt</b>, <b>.csv</b>, <b>.pdf</b>, <b>.docx</b> "
            "и <b>.xlsx</b> (только в <b>{}</b>).".format(msg.TXT_AI_ANALYST_ROLE_PHRASE),
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
        await _answer_local_pipeline_traceback(message)
        return

    max_len = settings.chat_max_message_chars
    raw = raw_user_text[:max_len]
    if dialog_text is not None:
        dialog_text = dialog_text[:max_len]

    table_context = raw if role_id == "table_generator" or is_xlsx_api_path else ""
    is_table_flow = role_id == "table_generator" or is_xlsx_api_path
    stream_handle = None

    if is_table_flow:
        if status_msg is None:
            status_msg = await _send_table_generator_status(message)
        async with flood_safe_chat_action_loop(deps.bot(), message.chat.id, "typing"):
            try:
                result = await run_chat_turn(
                    settings,
                    uid,
                    raw,
                    dialog_user_text=dialog_text,
                    user_image_data_url=user_image_data_url,
                    stream_callback=None,
                    text_role=role_id,
                    anchor_assistant_text=anchor_assistant_text,
                )
            except Exception:
                logger.exception("run_chat_turn table flow failed uid=%s", uid)
                result = ChatTurnResult(outcome=ChatTurnOutcome.AI_FAILED)

            if xlsx_fallback_rows:
                result = await _apply_table_graceful_degradation(
                    uid,
                    result,
                    fallback_rows=xlsx_fallback_rows,
                    fallback_worker=table_fallback_worker,
                    file_name=source_file_name,
                    title=table_fallback_title,
                    table_subrole=table_subrole,
                    column_structure_warning=column_structure_warning,
                )
    else:
        use_stream = (
            settings.telegram_chat_streaming
            and not is_photo
            and not user_image_data_url
        )
        async with bot_typing_indicator(deps.bot(), message.chat.id, platform="telegram"):
            stream_handle = (
                create_telegram_streaming_handle(
                    message,
                    deps.bot(),
                    edit_interval_sec=TELEGRAM_STREAM_EDIT_INTERVAL_SEC,
                )
                if use_stream
                else None
            )
            try:
                result = await run_chat_turn(
                    settings,
                    uid,
                    raw,
                    dialog_user_text=dialog_text,
                    user_image_data_url=user_image_data_url,
                    stream_callback=stream_handle.on_stream if stream_handle else None,
                    text_role=role_id,
                    anchor_assistant_text=anchor_assistant_text,
                )
            except Exception:
                logger.exception("run_chat_turn failed uid=%s role=%s", uid, role_id)
                result = ChatTurnResult(
                    outcome=ChatTurnOutcome.AI_FAILED,
                    effective_text_role=role_id,
                )

    if keep_waiting_state and result.outcome is ChatTurnOutcome.SUCCESS:
        await state.set_state(UserFlow.waiting_for_text_prompt)
        if result.effective_text_role:
            await state.update_data(text_role=result.effective_text_role)

    if result.outcome is ChatTurnOutcome.CONTEXT_TOO_LARGE:
        from platforms.image_menu_flow import try_free_photo_from_chat_overflow

        overflow_prompt = (forced_user_text or message.text or "").strip()
        if await try_free_photo_from_chat_overflow(
            message,
            state,
            prompt=overflow_prompt,
            user_id=uid,
        ):
            return

    await _reply_chat_turn_result(
        message,
        result,
        stream_handle=None if is_table_flow else stream_handle,
        table_context=table_context,
        prefer_table_error=is_xlsx_api_path,
        status_message=status_msg if is_table_flow else None,
        table_subrole=table_subrole if is_table_flow else None,
        reply_user_id=uid,
    )
