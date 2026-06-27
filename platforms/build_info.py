"""Версия деплоя для диагностики (git rev + UI-метки)."""

from __future__ import annotations

from pathlib import Path

from aiogram.enums import ParseMode
from aiogram.types import Message


def _git_rev_short() -> str:
    root = Path(__file__).resolve().parent.parent
    try:
        import subprocess

        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=root,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            .strip()
            or "unknown"
        )
    except Exception:
        return "unknown"


def _cfo_build_tag() -> str:
    try:
        from services.file_processor import _CFO_BUILD

        return str(_CFO_BUILD or "unknown")
    except Exception:
        return "unknown"


def get_build_info_text() -> str:
    from content import messages as msg

    rev = _git_rev_short()
    cfo = _cfo_build_tag()
    return (
        f"🛠 <b>NeuroMule</b> <code>{rev}</code> · CFO <code>{cfo}</code>\n"
        f"{msg.BTN_REPLY_NEUROTEXT} · {msg.BTN_TEXT_ROLE_TABLE}"
    )


def slash_command_base(text: str | None) -> str | None:
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    return raw.split()[0].split("@")[0].lower()


def is_gate_bypass_command(text: str | None) -> bool:
    """Команды, проходящие TOS/channel/terms gate (диагностика деплоя)."""
    return slash_command_base(text) in {"/start", "/version"}


async def reply_build_version(message: Message) -> None:
    await message.answer(get_build_info_text(), parse_mode=ParseMode.HTML)
