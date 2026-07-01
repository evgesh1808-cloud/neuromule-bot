"""Обрезка хвоста диалога перед отправкой в OpenRouter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def prune_context_messages(
    messages: list[dict[str, Any]],
    *,
    max_messages: int | None = None,
    max_tokens_est: int | None = None,
    estimate_tokens: Callable[[list[dict[str, Any]]], int] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Обрезает хвост диалога для OpenRouter.
    messages[0] — system (всегда сохраняется).

    Возвращает:
        Tuple[list, bool]: (модифицированный payload, флаг успешности fit_in_limit)
    """
    if not messages:
        return messages, True

    system = messages[0]
    tail = messages[1:]

    # Этап 1: Ограничение по количеству реплик (скользящее окно)
    if max_messages is not None and len(tail) > max_messages:
        tail = tail[-max_messages:]

    out = [system, *tail]

    # Этап 2: Ограничение по токенам (вырезаем старое из середины через pop(1))
    if max_tokens_est is not None and estimate_tokens is not None:
        while len(out) > 2 and estimate_tokens(out) > max_tokens_est:
            out.pop(1)

        # Проверяем финальный рубеж: уместился ли даже минимальный контекст?
        if estimate_tokens(out) > max_tokens_est:
            return out, False

    return out, True
