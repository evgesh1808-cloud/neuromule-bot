"""Discord-хендлеры саммаризатора (discord.py)."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

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
    "Отправьте текст, ссылку (YouTube/статья) или файл PDF/DOCX/TXT.\n"
    "Команды: `!start`, `!summary`"
)


async def _send_summary(channel: discord.abc.Messageable, result: SummarizeResult) -> None:
    if not result.ok:
        await channel.send(f"❌ {result.error_message}")
        return
    for chunk in chunk_text(result.summary):
        await channel.send(chunk)


def build_discord_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        logger.info("Summarizer Discord: logged in as %s", bot.user)

    @bot.command(name="start")
    async def cmd_start(ctx: commands.Context) -> None:
        await ctx.send(_START_TEXT)

    @bot.command(name="summary", aliases=["summarize"])
    async def cmd_summary(ctx: commands.Context) -> None:
        await ctx.send("Пришлите текст, ссылку или документ в следующем сообщении.")

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.content.startswith("!"):
            await bot.process_commands(message)
            return

        if message.attachments:
            attachment = message.attachments[0]
            ext = (attachment.filename or "").rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_FILE_EXTENSIONS:
                await message.reply("❌ Поддерживаются только PDF, DOCX и TXT.")
                return
            status = await message.reply("📥 Читаю файл...")
            try:
                data = await attachment.read()
                result = await summarize_from_file(data, ext)
            except Exception:
                logger.exception("discord summarizer attachment")
                result = SummarizeResult(ok=False, error_code="file", error_message="Ошибка чтения файла.")
            await status.delete()
            await _send_summary(message.channel, result)
            return

        text = (message.content or "").strip()
        if not text:
            return

        status = await message.reply("⏳ Обрабатываю...")
        raw, _kind = await resolve_raw_text(text)
        result = await summarize_text(raw)
        await status.delete()
        await _send_summary(message.channel, result)

    return bot


async def run_discord_summarizer() -> None:
    if not settings.discord_token.strip():
        raise RuntimeError("Задайте DISCORD_TOKEN в .env")
    bot = build_discord_bot()
    async with bot:
        await bot.start(settings.discord_token)
