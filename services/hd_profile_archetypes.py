"""Человекочитаемые архетипы HD-профилей (без сухих кодов 1/3, 3/5 в UI/промптах)."""
from __future__ import annotations

import re

# IHDS 12 профилей → понятные русские архетипы для пользователя.
HD_PROFILE_ARCHETYPES: dict[str, str] = {
    "1/3": "Исследователь-Практик",
    "1/4": "Исследователь-Сетевик",
    "2/4": "Отшельник-Сетевик",
    "2/5": "Отшельник-Спасатель",
    "3/5": "Экспериментатор-Спасатель",
    "3/6": "Экспериментатор-Наставник",
    "4/6": "Сетевик-Наставник",
    "4/1": "Сетевик-Исследователь",
    "5/1": "Спасатель-Исследователь",
    "5/2": "Спасатель-Отшельник",
    "6/2": "Наставник-Отшельник",
    "6/3": "Наставник-Практик",
}

# Расшифровка архетипа для промпта (человеческий язык без словаря HD).
HD_PROFILE_ARCHETYPE_HINTS: dict[str, str] = {
    "1/3": "фундамент знаний + обучение на собственных ошибках",
    "1/4": "глубина через сеть доверия и передачу опыта",
    "2/4": "талант, который раскрывается по приглашению",
    "2/5": "решения в кризисе под чужими проекциями спасителя",
    "3/5": "кризис-менеджер, от которого все ждут готовых решений",
    "3/6": "эксперименты сегодня — наставничество завтра",
    "4/6": "влияние через людей и зрелость роли наставника",
    "4/1": "сеть контактов + фундаментальная экспертиза",
    "5/1": "спасатель с исследовательской глубиной",
    "5/2": "решения в тишине, когда тебя «нашли»",
    "6/2": "мудрость наставника и право на уединение",
    "6/3": "наставник, который учится через практику и ошибки",
}

_PROFILE_CODE_RE = re.compile(r"\b[1-6]/[1-6]\b")

_PROFILE_ARCHETYPE_PROMPT_RULE = (
    "КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать сухие коды профилей (1/3, 3/5 и т.п.) в заголовках "
    "и основном тексте без человеческой расшифровки. ВСЕГДА используй архетип из user-блока "
    "(формат «Твой архетип: …»). Обычный пользователь должен понимать каждое слово без "
    "словаря Human Design."
)
PROFILE_ARCHETYPE_PROMPT_RULE = _PROFILE_ARCHETYPE_PROMPT_RULE


def normalize_profile_code(profile: str) -> str:
    text = (profile or "").strip().replace(" ", "")
    if not text:
        return ""
    match = re.search(r"([1-6])\s*[/\\-]\s*([1-6])", text)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return text


def profile_archetype_label(profile: str) -> str:
    """Возвращает архетип или исходную строку, если код неизвестен."""
    code = normalize_profile_code(profile)
    if code in HD_PROFILE_ARCHETYPES:
        return HD_PROFILE_ARCHETYPES[code]
    return code or (profile or "").strip()


def profile_archetype_hint(profile: str) -> str:
    code = normalize_profile_code(profile)
    return HD_PROFILE_ARCHETYPE_HINTS.get(code, "")


def format_profile_archetype_for_user(profile: str, *, with_hint: bool = True) -> str:
    label = profile_archetype_label(profile)
    if not label:
        return "не передан"
    hint = profile_archetype_hint(profile) if with_hint else ""
    if hint:
        return f"Твой архетип: {label} ({hint})"
    return f"Твой архетип: {label}"


def profile_llm_context_lines(profile: str) -> tuple[str, str]:
    """
    Returns:
        (server_code_line, archetype_line) для user_prompt LLM.
    """
    code = normalize_profile_code(profile)
    archetype = profile_archetype_label(profile)
    if not code and not archetype:
        return ("- Профиль (сервер): не передан", "- Архетип для текста: не передан")
    code_line = f"- Профиль (серверный код, НЕ цитируй в тексте): {code or profile}"
    hint = profile_archetype_hint(profile)
    archetype_line = f"- Архетип для пользователя (ОБЯЗАТЕЛЬНО вместо цифр): {archetype}"
    if hint:
        archetype_line = (
            f"- Архетип для пользователя (ОБЯЗАТЕЛЬНО вместо цифр): "
            f"Твой архетип: {archetype} ({hint})"
        )
    return code_line, archetype_line


def text_contains_raw_profile_code(text: str) -> bool:
    return bool(_PROFILE_CODE_RE.search(text or ""))
