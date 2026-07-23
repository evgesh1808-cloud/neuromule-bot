"""Premium Standard copy-pack: валидация, префикс и нормализация формата ответа."""

from __future__ import annotations

import re

_COPY_PACK_OPENER_RE = re.compile(
    r"готово!\s*разные\s*стили\s*на\s*выбор",
    re.IGNORECASE,
)
_PRE_BLOCK_RE = re.compile(r"<pre\b[^>]*>.*?</pre>", re.IGNORECASE | re.DOTALL)
_MD_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_-]+)?\s*\n?(.*?)```", re.DOTALL)
_COACH_MARKERS_RE = re.compile(
    r"(вы\s+можете\s+создать|отлично,?\s+что|📋\s*пример|"
    r"пример\s+поздравления|как\s+правильно\s+поздравить|"
    r"начните\s+с\s+обращения|выберите\s+подходящий\s+формат|"
    r"для\s+видео\s+с\s+танцем|шуточное\s+поздравление\s+[—\-])",
    re.IGNORECASE,
)

COPY_PACK_OPENER_LINE = (
    "Готово! Разные стили на выбор (нажмите на текст, чтобы скопировать):"
)

# Prefill для Gemini: модель продолжает уже начатый COPY PACK, а не уходит в коуч.
COPY_PACK_ASSISTANT_PREFIX = (
    f"{COPY_PACK_OPENER_LINE}\n\n"
    "🫀 <b>Эмоциональный и душевный</b>\n"
    "<pre>\n"
)

COPY_PACK_RETRY_USER = (
    "[ПЕРЕГЕНЕРАЦИЯ — ПРЕДЫДУЩИЙ ОТВЕТ ОТКЛОНЁН]\n"
    "Там была теория/коуч/советы — это ЗАПРЕЩЕНО.\n"
    "Продолжи ответ СТРОГО в формате COPY PACK с уже начатой первой строки "
    f"«{COPY_PACK_OPENER_LINE}» и блоками <pre>. Без советов и нумерации 1-2-3."
)


def convert_md_fences_to_pre(text: str) -> str:
    """Markdown ```…``` → Telegram ``<pre>…</pre>`` (модели часто путают формат)."""
    if not text or "```" not in text:
        return text

    def _repl(match: re.Match[str]) -> str:
        body = (match.group(1) or "").strip("\n")
        return f"<pre>\n{body}\n</pre>"

    return _MD_FENCE_RE.sub(_repl, text)


def merge_copy_pack_prefix(prefix: str, content: str) -> str:
    """Склеивает prefill + continuation, если opener ещё не в ответе."""
    text = (content or "").strip()
    if not text:
        return (prefix or "").rstrip()
    if _COPY_PACK_OPENER_RE.search(text):
        return text
    return f"{prefix}{text}"


def is_premium_copy_pack_reply(text: str) -> bool:
    """True, если ответ похож на copy-pack (opener + ≥3 ``<pre>``), без коуч-маркеров."""
    raw = (text or "").strip()
    if not raw:
        return False
    normalized = convert_md_fences_to_pre(raw)
    if not _COPY_PACK_OPENER_RE.search(normalized):
        return False
    if len(_PRE_BLOCK_RE.findall(normalized)) < 3:
        return False
    head = normalized.split("<pre", 1)[0]
    if _COACH_MARKERS_RE.search(head):
        return False
    return True


def looks_like_coach_reply(text: str) -> bool:
    """Грубый детект коуч-ответа без copy-pack."""
    raw = convert_md_fences_to_pre((text or "").strip())
    if not raw:
        return False
    if is_premium_copy_pack_reply(raw):
        return False
    if _COACH_MARKERS_RE.search(raw):
        return True
    if re.search(r"^\s*1\.\s", raw, re.MULTILINE) and "<pre>" not in raw.lower():
        return True
    return False


def normalize_copy_pack_reply(text: str) -> str:
    """Мягкая нормализация: fences→pre, без агрессивного markdown."""
    return convert_md_fences_to_pre(text or "").strip()
