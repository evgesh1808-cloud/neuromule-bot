"""Сервис отчёта по совместимости (Дизайн человека): только канал B — Gemini, без OpenRouter."""

from __future__ import annotations

import json

from services.hd_logic import gemini_generate_plain_text


async def generate_match_report(user1_data: dict[str, object], user2_data: dict[str, object]) -> str:
    """
    user1_data: {'type': 'Генератор', 'gates': '...'}
    user2_data: {'type': 'Проектор', 'gates': '...'}
    """
    prompt = (
        "Ты эксперт по Дизайну Человека. Сделай анализ совместимости (Композит).\n"
        f"Партнер 1: Тип {user1_data['type']}, Ворота: "
        f"{json.dumps(user1_data['gates'], ensure_ascii=False)}.\n"
        f"Партнер 2: Тип {user2_data['type']}, Ворота: "
        f"{json.dumps(user2_data['gates'], ensure_ascii=False)}.\n\n"
        "Напиши краткий, но глубокий отчет (до 2000 знаков):\n"
        "1. Формула отношений (где вы дополняете друг друга).\n"
        "2. Зоны конфликтов (где ваши энергии сталкиваются).\n"
        "3. Совет по деньгам: как вам вместе зарабатывать больше.\n"
        "4. Вердикт: главная цель вашего союза.\n\n"
        "Стиль: современный, без лишней мистики, только факты."
    )
    return await gemini_generate_plain_text(prompt)
