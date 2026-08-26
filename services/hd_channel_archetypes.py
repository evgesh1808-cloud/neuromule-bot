"""Человекочитаемые «Суперсилы» HD-каналов (без сухих кодов 20-34 в UI/промптах)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_CHANNEL_CODE_RE = re.compile(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\b")

# Кураторские названия суперсил (приоритет над JSON-библиотекой).
_HD_CHANNEL_SUPERPOWERS: dict[str, str] = {
    "1-8": "Суперсила творческого импульса",
    "2-14": "Суперсила направления ресурсов",
    "3-60": "Суперсила прорыва через тупик",
    "4-63": "Суперсила логической проверки",
    "5-15": "Суперсила естественного ритма",
    "6-59": "Суперсила эмоциональной близости",
    "7-31": "Суперсила лидерского влияния",
    "9-52": "Суперсила глубокой концентрации",
    "10-20": "Суперсила аутентичности в моменте",
    "10-34": "Суперсила сакральной самонаправленности",
    "10-57": "Суперсила интуитивного выживания",
    "11-56": "Суперсила живого storytelling",
    "12-22": "Суперсила эмоциональной выразительности",
    "13-33": "Суперсила мудрости прожитого опыта",
    "16-48": "Суперсила мастерства через практику",
    "17-62": "Суперсила структурирования идей",
    "18-58": "Суперсила улучшения систем",
    "19-49": "Суперсила чувствительности к ресурсам",
    "20-34": "Суперсила влияния в моменте",
    "20-57": "Суперсила мгновенной интуиции",
    "21-45": "Суперсила управления денежными потоками",
    "23-43": "Суперсила прорывных инсайтов",
    "24-61": "Суперсила глубинного понимания",
    "25-51": "Суперсила смелого первого шага",
    "26-44": "Суперсила рыночной памяти и продаж",
    "27-50": "Суперсила заботы и ответственности",
    "28-38": "Суперсила стойкости в борьбе за смысл",
    "29-46": "Суперсила удачи через верное «да»",
    "30-41": "Суперсила эмоционального обогащения",
    "32-54": "Суперсила амбициозного роста",
    "34-57": "Суперсила мощи с тонким чутьём",
    "35-36": "Суперсила насыщенного жизненного опыта",
    "37-40": "Суперсила семейных договорённостей",
    "39-55": "Суперсила эмоциональной глубины",
    "42-53": "Суперсила доведения циклов до конца",
    "47-64": "Суперсила ментального озарения",
}

_CHANNEL_ARCHETYPE_PROMPT_RULE = (
    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать сухие коды каналов (20-34, 34-20, 19-49 и т.п.) "
    "в заголовках и основном тексте. ВСЕГДА переводи канал в формат "
    "«Суперсила: [название человеческим языком]» из user-блока."
)
CHANNEL_ARCHETYPE_PROMPT_RULE = _CHANNEL_ARCHETYPE_PROMPT_RULE


def normalize_channel_code(channel: str) -> str:
    text = (channel or "").strip()
    if not text:
        return ""
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if len(nums) < 2:
        return text
    low, high = sorted(nums[:2])
    return f"{low}-{high}"


@lru_cache(maxsize=1)
def _channels_library_index() -> dict[str, dict[str, str]]:
    path = Path(__file__).resolve().parent.parent / "data" / "hd_blocks" / "channels.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def channel_superpower_label(channel: str) -> str:
    code = normalize_channel_code(channel)
    if code in _HD_CHANNEL_SUPERPOWERS:
        return _HD_CHANNEL_SUPERPOWERS[code]
    block = _channels_library_index().get(code)
    if isinstance(block, dict):
        title = str(block.get("title") or "").strip()
        if title:
            cleaned = re.sub(r"^Канал\s+\d+-\d+:\s*", "", title, flags=re.IGNORECASE)
            if cleaned and cleaned.lower() not in {"charisma", "struggle", "power"}:
                return f"Суперсила {cleaned.lower()}"
        gift = str(block.get("gift") or "").strip()
        if gift:
            return f"Суперсила: {gift.rstrip('.')}"
    return f"Суперсила канала {code}" if code else (channel or "").strip()


def format_channel_superpower_for_user(channel: str) -> str:
    label = channel_superpower_label(channel)
    if label.startswith("Суперсила:"):
        return label
    if label.startswith("Суперсила "):
        return label
    return f"Суперсила: {label}"


def channels_llm_context_block(active_channels: object) -> str:
    if not isinstance(active_channels, list) or not active_channels:
        return "- Активные каналы (суперсилы для текста): не переданы — не выдумывай"
    lines: list[str] = []
    for raw in active_channels:
        code = normalize_channel_code(str(raw))
        if not code:
            continue
        lines.append(
            f"  • Канал {code} (сервер, НЕ цитируй) → {format_channel_superpower_for_user(code)}"
        )
    if not lines:
        return "- Активные каналы (суперсилы для текста): не переданы — не выдумывай"
    return "- Активные каналы — переводи ТОЛЬКО как суперсилы:\n" + "\n".join(lines)


def text_contains_raw_channel_code(text: str) -> bool:
    return bool(_CHANNEL_CODE_RE.search(text or ""))
