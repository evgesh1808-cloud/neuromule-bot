"""Parallel premium chapter generation (Quiet Luxury multipass)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from services.hd_premium_context import build_premium_context
from services.hd_premium_prompts import (
    PREMIUM_CHAPTER_MAX_CHARS,
    PREMIUM_CHAPTER_MIN_CHARS,
    PREMIUM_CORE_KEYS,
    PREMIUM_EXTENDED_KEYS,
    build_fast_facts_prompt,
    build_premium_chapter_prompt,
)

logger = logging.getLogger(__name__)

_PARALLEL_CHAPTER_KEYS: tuple[str, ...] = (
    "genius_light",
    "mars_trauma",
    "false_self_masks",
    "phs_motivation",
    "incarnation_mission",
    "maturity_cycles",
    "money",
    "love",
    "energy",
    "dream_rave",
    "plan",
)


def _clamp_chapter_text(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) > PREMIUM_CHAPTER_MAX_CHARS:
        trimmed = cleaned[: PREMIUM_CHAPTER_MAX_CHARS - 1].rsplit(" ", 1)[0]
        return trimmed.rstrip() + "…"
    return cleaned


def _offline_chapter_stub(chapter_key: str, ctx: dict[str, Any]) -> str:
    """Минимальный офлайн-текст при недоступности LLM."""
    templates = {
        "genius_light": (
            "## Архетип гениальности\n\n"
            "Твоя карта несёт редкую конфигурацию силы. Определённые центры — "
            "это не случайность, а эволюционный актив, с которым ты пришёл в мир.\n\n"
            f"**Стратегия и Авторитет** — главный интерфейс принятия решений. "
            f"Каналы: {', '.join(str(c) for c in ctx.get('active_channels') or []) or 'уточняются по карте'}."
        ),
        "mars_trauma": (
            f"## Марсианская травма: {ctx.get('mars_trauma_label')}\n\n"
            "Сначала — твоя сила: ты способен трансформировать подсознательные паттерны в ресурс. "
            f"Линия Марса Дизайна .{ctx.get('mars_design_line')} указывает на тему «{ctx.get('mars_trauma_label')}». "
            "Ключ исцеления — через Внутренний Авторитет, без насилия над собой."
        ),
    }
    return templates.get(
        chapter_key,
        f"## {chapter_key}\n\nРаздел будет дополнен при следующей успешной AI-генерации.",
    )


async def generate_premium_chapter_markdown(
    chapter_key: str,
    *,
    user_name: str,
    math_data: dict[str, object],
    premium_ctx: dict[str, Any],
    user_gender: str = "",
    genius_excerpt: str = "",
    llm_call: Any,
) -> str:
    """Одна markdown-глава через переданный llm_call(system, user) -> str."""
    system_prompt, user_prompt = build_premium_chapter_prompt(
        chapter_key,
        user_name=user_name,
        math_data=math_data,
        premium_ctx=premium_ctx,
        user_gender=user_gender,
        genius_excerpt=genius_excerpt,
    )
    try:
        raw = await llm_call(system_prompt, user_prompt)
        text = _clamp_chapter_text(raw)
        if len(text) >= PREMIUM_CHAPTER_MIN_CHARS // 2:
            return text
        logger.warning("premium chapter %s too short (%s chars)", chapter_key, len(text))
        return text or _offline_chapter_stub(chapter_key, premium_ctx)
    except Exception:
        logger.exception("premium chapter %s LLM failed", chapter_key)
        return _offline_chapter_stub(chapter_key, premium_ctx)


async def generate_premium_report_quiet_luxury(
    user_name: str,
    math_data: dict[str, object],
    *,
    user_gender: str = "",
    llm_markdown_call: Any,
    llm_json_call: Any,
    energy_scales: dict[str, int],
    static_sections: dict[str, str],
) -> dict[str, Any]:
    """
    Quiet Luxury multipass: genius_light первым по смыслу, параллельная генерация глав.
    """
    premium_ctx = build_premium_context(math_data)

    async def _one(key: str, genius_excerpt: str = "") -> tuple[str, str]:
        text = await generate_premium_chapter_markdown(
            key,
            user_name=user_name,
            math_data=math_data,
            premium_ctx=premium_ctx,
            user_gender=user_gender,
            genius_excerpt=genius_excerpt,
            llm_call=llm_markdown_call,
        )
        return key, text

    genius_key, genius_text = await _one("genius_light")
    genius_excerpt = genius_text[:1200]

    other_keys = [k for k in _PARALLEL_CHAPTER_KEYS if k not in {"genius_light", "plan"}]
    parallel_results = await asyncio.gather(
        *[_one(k, genius_excerpt=genius_excerpt) for k in other_keys]
    )
    chapters: dict[str, str] = {genius_key: genius_text}
    for key, text in parallel_results:
        chapters[key] = text

    _, plan_text = await _one("plan", genius_excerpt=genius_excerpt)
    chapters["plan"] = plan_text

    excerpts = {k: chapters.get(k, "")[:500] for k in ("genius_light", "money", "love", "energy")}
    sys_ff, usr_ff = build_fast_facts_prompt(
        user_name=user_name,
        math_data=math_data,
        chapter_excerpts=excerpts,
        user_gender=user_gender,
    )
    fast_facts = ""
    try:
        raw_json = await llm_json_call(sys_ff, usr_ff)
        parsed = raw_json if isinstance(raw_json, dict) else {}
        if isinstance(raw_json, str):
            import json

            parsed = json.loads(re.search(r"\{.*\}", raw_json, flags=re.DOTALL).group(0))
        fast_facts = str(parsed.get("fast_facts") or "").strip()
    except Exception:
        logger.exception("fast_facts generation failed")
        fast_facts = (
            "⚡ Твоя карта — редкий код силы. "
            "💼 Финансовый рост через авторитет, не через давление. "
            "❤️ Отношения строятся на подлинности, не на маске."
        )

    report: dict[str, Any] = {
        "fast_facts": fast_facts,
        "money": chapters.get("money", ""),
        "love": chapters.get("love", ""),
        "energy": chapters.get("energy", ""),
        "plan": chapters.get("plan", ""),
        "energy_scales": energy_scales,
        "static_reference": static_sections,
    }
    for key in PREMIUM_EXTENDED_KEYS:
        if chapters.get(key):
            report[key] = chapters[key]

    report["synthesis_meta"] = {
        "quiet_luxury": True,
        "schema_version": 4,
        "chapters_ok": len([k for k in _PARALLEL_CHAPTER_KEYS if chapters.get(k)]),
        "parallel_domains": True,
    }
    return report
