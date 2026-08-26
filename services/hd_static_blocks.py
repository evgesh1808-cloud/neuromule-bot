"""Статическая библиотека IHDS-блоков для HD Premium (без LLM)."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BLOCKS_ROOT = _PROJECT_ROOT / "data" / "hd_blocks"

_CENTER_SLUGS: dict[str, str] = {
    "Голова": "head",
    "Аджна": "ajna",
    "Горло": "throat",
    "G-центр": "g_center",
    "Эго": "ego",
    "Селезенка": "spleen",
    "Солнечное сплетение": "solar_plexus",
    "Сакрал": "sacral",
    "Корень": "root",
}

_TYPE_SLUGS: dict[str, str] = {
    "генератор": "generator",
    "манифестирующий генератор": "manifesting_generator",
    "манифестор": "manifestor",
    "проектор": "projector",
    "рефлектор": "reflector",
}


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("hd_static_blocks: failed to read %s", path, exc_info=True)
        return None


@lru_cache(maxsize=1)
def _load_gates_index() -> dict[str, dict[str, str]]:
    data = _read_json(_BLOCKS_ROOT / "gates.json")
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    return {}


@lru_cache(maxsize=1)
def _load_channels_index() -> dict[str, dict[str, str]]:
    data = _read_json(_BLOCKS_ROOT / "channels.json")
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    return {}


@lru_cache(maxsize=1)
def _load_profiles_index() -> dict[str, dict[str, str]]:
    data = _read_json(_BLOCKS_ROOT / "profiles.json")
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    return {}


def _load_center_block(kind: str, center: str) -> dict[str, str] | None:
    slug = _CENTER_SLUGS.get(center)
    if not slug:
        return None
    data = _read_json(_BLOCKS_ROOT / "centers" / kind / f"{slug}.json")
    return data if isinstance(data, dict) else None


def _load_type_block(hd_type: str) -> dict[str, str] | None:
    normalized = (hd_type or "").strip().lower()
    slug = _TYPE_SLUGS.get(normalized)
    if not slug:
        for key, value in _TYPE_SLUGS.items():
            if key in normalized:
                slug = value
                break
    if not slug:
        return None
    data = _read_json(_BLOCKS_ROOT / "types" / f"{slug}.json")
    return data if isinstance(data, dict) else None


def _format_block_section(title: str, block: dict[str, str]) -> str:
    parts: list[str] = [title]
    for key in ("theme", "gift", "shadow", "not_self", "wisdom", "strategy_hint", "body"):
        value = str(block.get(key) or "").strip()
        if value:
            parts.append(value)
    return "\n".join(parts).strip()


def gate_block_text(gate: int, *, center: str = "") -> str:
    index = _load_gates_index()
    block = index.get(str(gate))
    if block:
        label = str(block.get("title") or f"Ворота {gate}").strip()
        return _format_block_section(label, block)
    center_hint = f" ({center})" if center else ""
    return (
        f"Ворота {gate}{center_hint}\n"
        "Ресурс: осознанное использование этой темы в повседневных решениях.\n"
        "Тень: автоматическое действие без паузы на телесный отклик.\n"
        "Компенсация: попытка доказать ценность через перегруз или контроль."
    )


def channel_block_text(channel: str) -> str:
    index = _load_channels_index()
    block = index.get(channel) or index.get(channel.replace("-", "—"))
    if block:
        label = str(block.get("title") or f"Канал {channel}").strip()
        return _format_block_section(label, block)
    return (
        f"Канал {channel}\n"
        "Устойчивая связь двух ворот: постоянный паттерн энергии и поведения в этой карте.\n"
        "Ресурс: предсказуемая сила, когда решения согласованы с авторитетом.\n"
        "Тень: действие по инерции, без проверки телесного сигнала."
    )


def profile_block_text(profile: str) -> str:
    index = _load_profiles_index()
    block = index.get(profile.strip())
    if block:
        return _format_block_section(f"Профиль {profile}", block)
    return (
        f"Профиль {profile}\n"
        "Сочетание сознательной и бессознательной линии: стиль обучения, ошибок и зрелости.\n"
        "Используй профиль как фильтр темпа — не как оправдание избегания действий."
    )


def type_block_text(hd_type: str) -> str:
    block = _load_type_block(hd_type)
    if block:
        return _format_block_section(f"Тип: {hd_type}", block)
    return (
        f"Тип: {hd_type}\n"
        "Базовая механика принятия решений и распределения энергии.\n"
        "Опирайся на переданную стратегию и авторитет, а не на социальные ожидания."
    )


def center_block_text(center: str, *, defined: bool) -> str:
    kind = "defined" if defined else "open"
    block = _load_center_block(kind, center)
    if block:
        state = "определённый" if defined else "открытый"
        return _format_block_section(f"Центр «{center}» ({state})", block)
    state = "устойчивый ресурс" if defined else "зона обучаемости и чужих программ"
    return f"Центр «{center}»: {state}."


def collect_gate_numbers(gates: object) -> list[int]:
    nums: list[int] = []
    if not isinstance(gates, dict):
        return nums
    seen: set[int] = set()
    for payload in gates.values():
        if isinstance(payload, dict):
            gate = payload.get("gate")
            if isinstance(gate, int) and gate not in seen:
                seen.add(gate)
                nums.append(gate)
    return sorted(nums)


def assemble_static_reference(
    math_data: dict[str, object],
    *,
    gate_to_center: dict[int, str] | None = None,
) -> dict[str, str]:
    """
    Собирает статические секции отчёта по math_data (0 LLM).

    Returns:
        dict section_key → plain text для PDF и склейки глав.
    """
    gate_map = gate_to_center or {}
    hd_type = str(math_data.get("hd_type") or "").strip()
    profile = str(math_data.get("profile") or "").strip()
    authority = str(math_data.get("authority") or "").strip()
    strategy = str(math_data.get("strategy") or "").strip()
    definition = str(math_data.get("definition") or "").strip()

    sections: dict[str, str] = {}

    if hd_type:
        sections["type"] = type_block_text(hd_type)
    if profile:
        sections["profile"] = profile_block_text(profile)

    meta_lines: list[str] = []
    if strategy:
        meta_lines.append(f"Стратегия: {strategy}")
    if authority:
        meta_lines.append(f"Авторитет: {authority}")
    if definition:
        meta_lines.append(f"Определённость: {definition}")
    if meta_lines:
        sections["mechanics"] = "\n".join(meta_lines)

    gate_lines: list[str] = []
    for gate in collect_gate_numbers(math_data.get("gates")):
        center = gate_map.get(gate, "")
        gate_lines.append(gate_block_text(gate, center=center))
    if gate_lines:
        sections["gates"] = "\n\n".join(gate_lines)

    channel_lines: list[str] = []
    for ch in math_data.get("active_channels") or []:
        label = str(ch).strip()
        if label:
            channel_lines.append(channel_block_text(label))
    if channel_lines:
        sections["channels"] = "\n\n".join(channel_lines)

    defined = list(math_data.get("defined_centers") or [])
    open_centers = list(math_data.get("open_centers") or [])
    defined_lines = [center_block_text(name, defined=True) for name in defined]
    open_lines = [center_block_text(name, defined=False) for name in open_centers]
    if defined_lines:
        sections["centers_defined"] = "\n\n".join(defined_lines)
    if open_lines:
        sections["centers_open"] = "\n\n".join(open_lines)

    return sections


def format_static_reference_full(sections: dict[str, str]) -> str:
    """Полный статический блок для PDF-справочника."""
    order = ("type", "profile", "mechanics", "centers_defined", "centers_open", "channels", "gates")
    parts: list[str] = []
    for key in order:
        text = str(sections.get(key) or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def format_static_reference_for_domain(sections: dict[str, str], domain: str) -> str:
    """Кontекст static-блоков, релевантный domain-главе."""
    _ = domain
    core = format_static_reference_full(
        {k: sections[k] for k in ("type", "profile", "mechanics") if k in sections}
    )
    centers = "\n\n".join(
        text
        for text in (
            sections.get("centers_defined", ""),
            sections.get("centers_open", ""),
        )
        if text.strip()
    )
    channels = sections.get("channels", "")
    parts = [part for part in (core, centers, channels) if part.strip()]
    return "\n\n".join(parts).strip()


def static_reference_page_chunks(sections: dict[str, str], *, chars_per_page: int = 2200) -> list[str]:
    """Разбивает static reference на «страницы» для оценки объёма PDF."""
    full = format_static_reference_full(sections)
    if not full:
        return []
    chunks: list[str] = []
    paragraphs = full.split("\n\n")
    buffer = ""
    for para in paragraphs:
        candidate = f"{buffer}\n\n{para}".strip() if buffer else para
        if len(candidate) > chars_per_page and buffer:
            chunks.append(buffer.strip())
            buffer = para
        else:
            buffer = candidate
    if buffer.strip():
        chunks.append(buffer.strip())
    return chunks
