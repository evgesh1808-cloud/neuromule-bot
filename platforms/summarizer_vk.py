"""VK-хендлеры саммаризатора (vkbottle)."""
from __future__ import annotations

import logging

from config import settings
from core.summarizer import (
    ALLOWED_FILE_EXTENSIONS,
    SummarizeResult,
    chunk_text,
    resolve_raw_text,
    summarize_from_file,
    summarize_text,
)

logger = logging.getLogger(__name__)

_START_TEXT = (
    "👋 Саммаризатор NeuroMule.\n"
    "Текст, ссылка (YouTube/статья) или документ PDF/DOCX/TXT."
)


async def _reply_summary(message: object, result: SummarizeResult) -> None:
    answer = getattr(message, "answer", None)
    if answer is None:
        return
    if not result.ok:
        await answer(f"❌ {result.error_message}")
        return
    for chunk in chunk_text(result.summary):
        await answer(chunk)


def register_summarizer_vk(bot: object) -> None:
    """Регистрация хендлеров на экземпляре vkbottle.Bot."""
    from vkbottle.bot import Message

    @bot.on.message()
    async def summarizer_handler(message: Message) -> None:
        text = (message.text or "").strip()

        if text.startswith("/start") or text.lower() in {"/summary", "/summarize"}:
            await message.answer(_START_TEXT)
            return

        if not text and not message.attachments:
            return

        if message.attachments:
            doc = next((a.doc for a in message.attachments if getattr(a, "doc", None)), None)
            if doc is None:
                await message.answer("❌ Пришлите документ PDF, DOCX или TXT.")
                return
            title = (doc.title or "file").lower()
            ext = title.rsplit(".", 1)[-1] if "." in title else ""
            if ext not in ALLOWED_FILE_EXTENSIONS:
                await message.answer("❌ Поддерживаются только PDF, DOCX и TXT.")
                return
            try:
                import httpx

                url = doc.url
                if not url:
                    await message.answer("❌ Не удалось получить ссылку на файл VK.")
                    return
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.content
                result = await summarize_from_file(data, ext)
            except Exception:
                logger.exception("vk summarizer attachment")
                result = SummarizeResult(ok=False, error_code="file", error_message="Ошибка чтения файла.")
            await _reply_summary(message, result)
            return

        if text.startswith("/"):
            return

        status = await message.answer("⏳ Обрабатываю...")
        raw, _kind = await resolve_raw_text(text)
        result = await summarize_text(raw)
        try:
            await status.delete()
        except Exception:
            pass
        await _reply_summary(message, result)


def run_vk_summarizer_blocking() -> None:
    if not settings.vk_token.strip():
        raise RuntimeError("Задайте VK_TOKEN в .env")

    try:
        from vkbottle.bot import Bot
    except ImportError as exc:
        raise RuntimeError("Установите vkbottle: pip install vkbottle") from exc

    bot = Bot(token=settings.vk_token)
    register_summarizer_vk(bot)
    logger.info("Summarizer VK: polling started")
    bot.run_forever()
