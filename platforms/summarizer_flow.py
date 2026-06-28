"""Интеграция core/summarizer в NeuroMule (кнопка «📄 Саммари» в ИИ-Ассистенте)."""
from __future__ import annotations

import logging
from io import BytesIO

from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings
from content import messages as msg
from core.summarizer import (
    ALLOWED_FILE_EXTENSIONS,
    SummarizeResult,
    chunk_text,
    resolve_raw_text,
    summarize_from_file,
    summarize_text,
)
from platforms.handlers import deps
from platforms.telegram_states import UserFlow
from services import payments_catalog as paycat
from services.billing import billing
from services.billing.store import refund_charge
from services.rate_limit_service import allow_request

logger = logging.getLogger(__name__)

_SUMMARY_HINT = (
    "📄 <b>Режим «Саммари» включён</b>\n\n"
    "Пришлите одним сообщением:\n"
    "• текст статьи или лекции\n"
    "• ссылку на <b>YouTube</b> или сайт\n"
    "• файл <b>PDF</b>, <b>DOCX</b> или <b>TXT</b>\n\n"
    "Выжимка — по шаблону Senior BA (gpt-4o-mini)."
)

_STATUS = {
    "plain": "🤖 <b>ИИ анализирует текст…</b>",
    "youtube": "📺 <b>Скачиваю субтитры YouTube…</b>",
    "article": "🌐 <b>Читаю статью…</b>",
    "file": "📥 <b>Читаю файл…</b>",
}


async def send_summary_mode_hint(message: Message) -> None:
    await message.answer(_SUMMARY_HINT, parse_mode=ParseMode.HTML)


async def _send_summary_chunks(message: Message, result: SummarizeResult) -> None:
    for chunk in chunk_text(result.summary):
        try:
            await message.answer(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.answer(chunk)


async def _fail(
    message: Message,
    status_msg: Message | None,
    text: str,
    *,
    charge_id: str | None,
) -> None:
    if charge_id:
        await refund_charge(charge_id)
    if status_msg is not None:
        try:
            await status_msg.delete()
        except Exception:
            pass
    await message.answer(text, parse_mode=ParseMode.HTML)


async def handle_summary_neurotext_message(
    message: Message,
    state: FSMContext,
    *,
    keep_waiting_state: bool = True,
) -> None:
    """Текст / ссылка / PDF|DOCX|TXT → core.summarizer (без OpenRouter-чата)."""
    uid = message.from_user.id
    is_document = bool(message.document)
    is_photo = bool(message.photo)

    if is_photo:
        await message.answer(
            "⚠️ В режиме <b>Саммари</b> отправьте текст, ссылку или документ PDF/DOCX/TXT.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not settings.openai_api_key.strip():
        await message.answer(
            "⚠️ Саммаризатор временно недоступен (не задан <code>OPENAI_API_KEY</code>).",
            parse_mode=ParseMode.HTML,
        )
        return

    if not await allow_request(settings, uid, settings.chat_rate_limit_per_minute):
        await message.answer(msg.TXT_CHAT_RATE_LIMIT, parse_mode=ParseMode.HTML)
        return

    billing_result = await billing.resolve_and_charge_text_chat(uid, "summary")
    charge_id = billing_result.charge_id
    if billing_result.plan.blocked:
        if billing_result.plan.block_reason == "expert_role_requires_paid_tariff":
            await message.answer(
                msg.TXT_CHAT_EXPERT_INSUFFICIENT,
                reply_markup=paycat.shop_packages_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return
        await message.answer(
            msg.TXT_CHAT_ZERO_BALANCE_PREMIUM,
            reply_markup=paycat.shop_packages_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg: Message | None = None
    try:
        if is_document:
            file_name = (message.document.file_name or "document").strip()
            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
            if ext not in ALLOWED_FILE_EXTENSIONS:
                await _fail(
                    message,
                    None,
                    "⚠️ В режиме <b>Саммари</b> поддерживаются только <b>PDF</b>, <b>DOCX</b> и <b>TXT</b>.",
                    charge_id=charge_id,
                )
                return
            status_msg = await message.answer(_STATUS["file"], parse_mode=ParseMode.HTML)
            bot = deps.bot()
            file = await bot.get_file(message.document.file_id)
            buffer = BytesIO()
            await bot.download_file(file.file_path, buffer)
            result = await summarize_from_file(buffer.getvalue(), ext)
        else:
            user_text = (message.text or "").strip()
            if not user_text:
                await _fail(message, None, "⚠️ Пришлите текст или ссылку.", charge_id=charge_id)
                return
            status_msg = await message.answer("⏳ <b>Обрабатываю…</b>", parse_mode=ParseMode.HTML)
            raw, kind = await resolve_raw_text(user_text)
            if kind == "youtube" and not raw:
                await _fail(
                    message,
                    status_msg,
                    "❌ Не удалось скачать субтитры с YouTube. "
                    "Проверьте, что у видео включены субтитры (RU или EN).",
                    charge_id=charge_id,
                )
                return
            if kind != "plain":
                try:
                    await status_msg.edit_text(_STATUS[kind], parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            result = await summarize_text(raw)

        if not result.ok:
            await _fail(
                message,
                status_msg,
                f"❌ {result.error_message}",
                charge_id=charge_id,
            )
            return

        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass
        await _send_summary_chunks(message, result)
    except Exception:
        logger.exception("summary neurotext failed uid=%s", uid)
        await _fail(
            message,
            status_msg,
            msg.TXT_GEN_JOB_FAILED,
            charge_id=charge_id,
        )
        return

    if keep_waiting_state:
        await state.set_state(UserFlow.waiting_for_text_prompt)
        await state.update_data(text_role="summary")
