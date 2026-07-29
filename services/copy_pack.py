"""Premium Standard copy-pack: валидация, префикс и нормализация формата ответа."""

from __future__ import annotations

import re

_COPY_PACK_OPENER_RE = re.compile(
    r"готово!\s*разные\s*(?:стили|варианты)\s*на\s*выбор|"
    r"готово!\s*.{0,40}на\s*выбор|"
    r"разные\s+варианты\s+на\s+выбор",
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
    "Готово! Разные варианты на выбор (нажмите на текст, чтобы скопировать):"
)

# Prefill: только opener — заголовки модель придумывает сама (без фиксированных психотипов).
COPY_PACK_ASSISTANT_PREFIX = f"{COPY_PACK_OPENER_LINE}\n\n"

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


def count_pre_blocks(text: str) -> int:
    normalized = convert_md_fences_to_pre(text or "")
    return len(_PRE_BLOCK_RE.findall(normalized))


def is_premium_copy_pack_reply(text: str) -> bool:
    """True, если ответ — пак вариантов (≥3 ``<pre>``), не коуч-теория."""
    raw = (text or "").strip()
    if not raw:
        return False
    normalized = convert_md_fences_to_pre(raw)
    pre_n = len(_PRE_BLOCK_RE.findall(normalized))
    if pre_n < 3:
        return False
    head = normalized.split("<pre", 1)[0]
    # Коуч во вступлении до первого <pre> — не считаем copy-pack.
    if _COACH_MARKERS_RE.search(head):
        return False
    return True


def suppress_suggested_replies_for_answer(text: str) -> bool:
    """Не показывать follow-up кнопки под COPY PACK / паком вариантов."""
    return is_premium_copy_pack_reply(text)


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
