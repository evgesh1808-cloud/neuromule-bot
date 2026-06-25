"""Версия деплоя для диагностики (git rev + UI-метки)."""

from __future__ import annotations

from pathlib import Path


def get_build_info_text() -> str:
    from content import messages as msg

    root = Path(__file__).resolve().parent.parent
    rev = "unknown"
    try:
        import subprocess

        rev = (
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
        pass
    return (
        f"🛠 <b>NeuroMule build</b>\n"
        f"<code>rev={rev}</code>\n"
        f"ui={msg.BTN_REPLY_NEUROTEXT!r}\n"
        f"table={msg.BTN_TEXT_ROLE_TABLE!r}\n"
        f"studio={msg.BTN_STUDIO_MENU!r}"
    )
