"""Sanitizers for user-facing HD report text."""
from __future__ import annotations

import re

from services.hd_channel_archetypes import (
    format_channel_superpower_for_user,
    normalize_channel_code,
)
from services.hd_profile_archetypes import profile_archetype_label

_PROFILE_CODE_RE = re.compile(r"\b[1-6]/[1-6]\b")
_CHANNEL_CODE_RE = re.compile(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b")

_PROTECTED_SNIPPETS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[Дд]ни\s+1\s*[-–]\s*5\b"), "__HD_DAYS_1_5__"),
    (re.compile(r"\b[Дд]ни\s+6\s*[-–]\s*15\b"), "__HD_DAYS_6_15__"),
    (re.compile(r"\b[Дд]ни\s+16\s*[-–]\s*30\b"), "__HD_DAYS_16_30__"),
    (re.compile(r"\b1\s*[-–]\s*5\b(?!\s*минут)"), "__HD_RANGE_1_5__"),
    (re.compile(r"\b6\s*[-–]\s*15\b"), "__HD_RANGE_6_15__"),
    (re.compile(r"\b16\s*[-–]\s*30\b"), "__HD_RANGE_16_30__"),
    (re.compile(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\s*минут\b", re.IGNORECASE), "__HD_MINUTES__"),
)


def _normalize_active_channels(active_channels: object) -> set[str]:
    if not isinstance(active_channels, (list, tuple, set, frozenset)):
        return set()
    normalized: set[str] = set()
    for item in active_channels:
        code = normalize_channel_code(str(item))
        if code:
            normalized.add(code)
    return normalized


def _protect_plain_ranges(text: str) -> tuple[str, dict[str, str]]:
    protected = text
    tokens: dict[str, str] = {}
    for idx, (pattern, token) in enumerate(_PROTECTED_SNIPPETS):

        def _stash(match: re.Match[str], *, tok: str = token, i: int = idx) -> str:
            key = f"{tok}_{i}_{len(tokens)}"
            tokens[key] = match.group(0)
            return key

        protected = pattern.sub(_stash, protected)
    return protected, tokens


def _restore_plain_ranges(text: str, tokens: dict[str, str]) -> str:
    restored = text
    for key, original in tokens.items():
        restored = restored.replace(key, original)
    return restored


def sanitize_hd_user_facing_text(
    text: str,
    *,
    active_channels: object = None,
) -> str:
    """
    Заменяет сухие коды профилей и каналов на человеческие архетипы.

    Каналы заменяются только если код входит в ``active_channels`` карты пользователя.
    Диапазоны дней плана и минутные интервалы не трогаются.
    """
    if not text:
        return text

    channel_whitelist = _normalize_active_channels(active_channels)
    protected, tokens = _protect_plain_ranges(text)

    def _profile_repl(match: re.Match[str]) -> str:
        return profile_archetype_label(match.group(0))

    out = _PROFILE_CODE_RE.sub(_profile_repl, protected)

    def _channel_repl(match: re.Match[str]) -> str:
        code = normalize_channel_code(match.group(0))
        if code and code in channel_whitelist:
            return format_channel_superpower_for_user(code)
        return match.group(0)

    out = _CHANNEL_CODE_RE.sub(_channel_repl, out)
    return _restore_plain_ranges(out, tokens)


# Backward-compatible alias for hd_logic imports/tests.
_sanitize_hd_user_facing_text = sanitize_hd_user_facing_text
