"""Premium Standard copy-pack: валидация и нормализация формата ответа."""

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
    r"начните\s+с\s+обращения|выберите\s+подходящий\s+формат)",
    re.IGNORECASE,
)

COPY_PACK_RETRY_USER = (
    "[ПЕРЕГЕНЕРАЦИЯ — ПРЕДЫДУЩИЙ ОТВЕТ ОТКЛОНЁН]\n"
    "Там была теория/коуч/советы — это ЗАПРЕЩЕНО.\n"
    "Выдай ТОЛЬКО COPY PACK:\n"
    "1) Первая строка точно: Готово! Разные стили на выбор (нажмите на текст, чтобы скопировать):\n"
    "2) Ровно 4 блока: эмодзи + <b>название стиля</b> + <pre>готовый текст</pre>\n"
    "Стили: Эмоциональный и душевный / Официальный и деловой / "
    "Ультра-короткий экспресс / Современный / С юмором.\n"
    "Без «Вы можете…», без пунктов 1-2-3, без «Пример»."
)


def convert_md_fences_to_pre(text: str) -> str:
    """Markdown ```…``` → Telegram ``<pre>…</pre>`` (модели часто путают формат)."""
    if not text or "```" not in text:
        return text

    def _repl(match: re.Match[str]) -> str:
        body = (match.group(1) or "").strip("\n")
        return f"<pre>\n{body}\n</pre>"

    return _MD_FENCE_RE.sub(_repl, text)


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
    # Коуч-маркеры до первого <pre> — явный провал формата.
    head = normalized.split("<pre", 1)[0]
    if _COACH_MARKERS_RE.search(head):
        return False
    return True


def normalize_copy_pack_reply(text: str) -> str:
    """Мягкая нормализация: fences→pre, без агрессивного markdown."""
    return convert_md_fences_to_pre(text or "").strip()
