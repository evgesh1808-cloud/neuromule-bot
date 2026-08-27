"""Quiet Luxury premium prompts — existential psychoanalysis + biohacking tone."""
from __future__ import annotations

from typing import Any

from services.hd_premium_context import PHS_ENVIRONMENT, build_premium_context

PREMIUM_CHAPTER_MIN_CHARS = 5000
PREMIUM_CHAPTER_MAX_CHARS = 6500

PREMIUM_EXTENDED_KEYS: tuple[str, ...] = (
    "genius_light",
    "mars_trauma",
    "false_self_masks",
    "phs_motivation",
    "incarnation_mission",
    "maturity_cycles",
    "dream_rave",
)

PREMIUM_CORE_KEYS: tuple[str, ...] = ("fast_facts", "money", "love", "energy", "plan")

PREMIUM_PDF_CHAPTER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("genius_light", "Анатомия истинного Я: архетип гениальности", "hd_ch_genius"),
    ("mars_trauma", "Марсианская травма подсознания", "hd_ch_mars"),
    ("false_self_masks", "Сорванные маски: ловушки ложного Я", "hd_ch_false_self"),
    ("phs_motivation", "Код подсознательной мотивации и супер-чувство", "hd_ch_phs"),
    ("incarnation_mission", "Эволюционная миссия и декорации судьбы", "hd_ch_mission"),
    ("maturity_cycles", "Карта зрелости: планетарные кризисы и экзамены", "hd_ch_maturity"),
    ("money", "Финансовый аудит и формула триумфа", "hd_ch_money"),
    ("love", "Отношения, интимность и групповая динамика", "hd_ch_love"),
    ("energy", "Энергетическая архитектура и биохакинг тела", "hd_ch_energy"),
    ("dream_rave", "Анатомия ночного сна", "hd_ch_dream"),
    ("plan", "План интеграции на 30 дней", "hd_ch_plan"),
)

_QUIET_LUXURY_TOV = (
    "TONE OF VOICE — QUIET LUXURY × NEUROMULE HD:\n"
    "Ты — премиальный коуч-аналитик на стыке экзистенциального психоанализа и биохакинга. "
    "Стиль: Apple, The Pattern, Co-Star — минимализм, точность, уважение к интеллекту клиента.\n"
    "Язык: богатый русский, короткие абзацы, много «воздуха», markdown-иерархия (##, ###, списки).\n"
    "ЗАПРЕЩЕНО: эзотерическая вода, «слушай себя», «верь во вселенную», шаблонный коучинг, "
    "англицизмы без перевода, сырые коды каналов в тексте.\n\n"
    "СВЯЩЕННОЕ ПРАВИЛО ПОДАЧИ:\n"
    "Любой разбор КАТЕГОРИЧЕСКИ начинается с главного и положительного. "
    "Сначала — масштаб, потенциал, эволюционное преимущество. "
    "Только после мощного позитивного фундамента — элегантный переход к зонам роста "
    "(не дефекты, а скрытые вызовы и будущая мудрость).\n"
)

_CHAPTER_BRIEFS: dict[str, str] = {
    "genius_light": (
        "Глава «АНАТОМИЯ ИСТИННОГО Я: АРХЕТИП ГЕНИАЛЬНОСТИ». "
        "Манифест силы: определённые центры, каналы, Стратегия и Авторитет как "
        "ультимативное эволюционное оружие по праву рождения. Только триумф и масштаб."
    ),
    "mars_trauma": (
        "Глава «МАРСИАНСКАЯ ТРАВМА ПОДСОЗНАНИЯ». "
        "На основе линии Красного Марса (Design Mars .1–.6) разбери одну из 6 "
        "Генетических Травм. Начни с 2–3 предложений силы, затем — как блок саботирует "
        "финансы. Заверши «Ключом исцеления» через Внутренний Авторитет."
    ),
    "false_self_masks": (
        "Глава «СОРВАННЫЕ МАСКИ: ЛОВУШКИ ЛОЖНОГО Я». "
        "Открытые центры через голоса Ложного Я (Сакрал — упахиваться, Эго — доказывать, "
        "Корень — спешить). Коуч-практика отсечения ментального шума. "
        "Тени — зоны роста, не дефекты."
    ),
    "phs_motivation": (
        "Глава «КОД ПОДСОЗНАТЕЛЬНОЙ МОТИВАЦИИ И СУПЕР-ЧУВСТВО». "
        "Истинная Мотивация по Цвету Солнца Личности (1–6), ловушка Переноса ума. "
        "Супер-Чувство по Тону Солнца Личности — радар истины быстрее логики."
    ),
    "incarnation_mission": (
        "Глава «ЭВОЛЮЦИОННАЯ МИССИЯ И ДЕКОРАЦИИ СУДЬБЫ». "
        "Инкарнационный Крест как 4 Столпа Судьбы. Таймлайн среды: Акт 1 (0–40, Южный Узел), "
        "Кризис Квантового Перехода (40–42), Акт 2 (42+, Северный Узел)."
    ),
    "maturity_cycles": (
        "Глава «КАРТА ЗРЕЛОСТИ: ПЛАНЕТАРНЫЕ КРИЗИСЫ И ЭКЗАМЕНЫ». "
        "Возврат Сатурна (29–30), Оппозиция Урана (38–42), Возврат Хирона (50). "
        "Если в Профиле есть линия 6 — три фазы жизни (до 30, 30–50, 50+)."
    ),
    "money": (
        "Глава «ФИНАНСОВЫЙ АУДИТ». Сквозной синтез карты в единый психологический узор. "
        "Встроить: Социальный аудит Пенты (роль в группах до 5 человек) + "
        "Формула триумфа Юпитера (законы процветания и жёсткие табу)."
    ),
    "love": (
        "Глава «ОТНОШЕНИЯ И ИНТИМНОСТЬ». Сквозной синтез. "
        "Пента в паре/семье + точка глубокого кризиса Плутона как место главных побед."
    ),
    "energy": (
        "Глава «ЭНЕРГЕТИЧЕСКАЯ АРХИТЕКТУРА». "
        "Модуль PHS-биохакинга: Режим Питания по Цвету/Тону Солнца/Земли Дизайна "
        "(Охотник, Собиратель, Жажда, Прикосновение, Звук, Свет) с запретами + "
        "Идеальная Среда Восстановления (Пещеры, Рынки, Кухни, Горы, Долины, Берега)."
    ),
    "dream_rave": (
        "Глава «АНАТОМИЯ НОЧНОГО СНА (DREAM RAVE)». "
        "5-центровая ночная матрица: Ночной Тип, Порталы Сна по активным ночным воротам, "
        "Протокол Пробуждения."
    ),
    "plan": (
        "План интеграции на 30 дней: три блока (дни 1–5 / 6–15 / 16–30), "
        "SMART-вызовы, соматические стоп-сигналы. Без сухого to-do."
    ),
}


def _format_context_block(math_data: dict[str, object], ctx: dict[str, Any]) -> str:
    hd_type = str(math_data.get("hd_type") or "")
    profile = str(math_data.get("profile") or "")
    authority = str(math_data.get("authority") or "")
    strategy = str(math_data.get("strategy") or "")
    defined = ", ".join(str(c) for c in ctx.get("defined_centers") or [])
    open_c = ", ".join(str(c) for c in ctx.get("open_centers") or [])
    channels = ", ".join(str(c) for c in ctx.get("active_channels") or [])
    env = PHS_ENVIRONMENT.get(int(ctx.get("phs_environment_color") or 1), "Долины")
    return (
        f"- Тип: {hd_type}\n"
        f"- Профиль: {profile}\n"
        f"- Авторитет: {authority}\n"
        f"- Стратегия: {strategy}\n"
        f"- Определённые центры: {defined or '—'}\n"
        f"- Открытые центры: {open_c or '—'}\n"
        f"- Активные каналы: {channels or '—'}\n"
        f"- Марс Дизайна линия .{ctx.get('mars_design_line')} → Травма: {ctx.get('mars_trauma_label')}\n"
        f"- PHS Мотивация (Цвет Солнца Личности {ctx.get('personality_sun_color')}): {ctx.get('phs_motivation')}\n"
        f"- PHS Супер-Чувство (Тон {ctx.get('personality_sun_tone')}): {ctx.get('phs_cognition')}\n"
        f"- PHS Питание (Цвет Солнца Дизайна {ctx.get('design_sun_color')}): {ctx.get('phs_determination')}\n"
        f"- PHS Среда: {env}\n"
        f"- Юпитер: ворота {ctx.get('jupiter_gate')} линия {ctx.get('jupiter_line')}\n"
        f"- Плутон: ворота {ctx.get('pluto_gate')} линия {ctx.get('pluto_line')}\n"
        f"- Крест (4 ворота): {ctx.get('incarnation_cross_gates')}\n"
        f"- Пента-ворота в карте: {ctx.get('penta_active_gates')}\n"
        f"- Dream Rave активности: {ctx.get('dream_rave_gates')}\n"
        f"- Профиль линия 6: {'да' if ctx.get('profile_has_line_6') else 'нет'}"
    )


def build_premium_chapter_prompt(
    chapter_key: str,
    *,
    user_name: str,
    math_data: dict[str, object],
    premium_ctx: dict[str, Any] | None = None,
    user_gender: str = "",
    genius_excerpt: str = "",
) -> tuple[str, str]:
    """System + user prompt для одной markdown-главы premium-отчёта."""
    key = (chapter_key or "").strip().lower()
    brief = _CHAPTER_BRIEFS.get(key)
    if not brief:
        raise ValueError(f"unknown premium chapter: {chapter_key!r}")

    ctx = premium_ctx if premium_ctx is not None else build_premium_context(math_data)
    name = (user_name or "").strip() or "клиент"
    context_block = _format_context_block(math_data, ctx)

    shadow_keys = {"mars_trauma", "false_self_masks", "dream_rave"}
    positive_first_rule = (
        "Начни главу с 2–4 предложений силы и масштаба — влюби читателя в свою механику.\n"
        if key in shadow_keys
        else "Эта глава — чистый свет: только потенциал, преимущества, триумф.\n"
    )
    if genius_excerpt.strip() and key in shadow_keys:
        positive_first_rule += (
            f"Уже дан позитивный фундамент (фрагмент): {genius_excerpt[:800]}…\n"
            "Сделай элегантный переход от силы к зоне роста.\n"
        )

    gender_note = ""
    g = (user_gender or "").strip().lower()
    if g in {"f", "female", "жен", "ж"}:
        gender_note = "Пол клиента: женский — соблюдай женский род.\n"
    elif g in {"m", "male", "м", "муж"}:
        gender_note = "Пол клиента: мужской — соблюдай мужской род.\n"

    system_prompt = (
        f"{_QUIET_LUXURY_TOV}\n"
        f"{positive_first_rule}\n"
        "ФОРМАТ ВЫДАЧИ: чистый Markdown-текст (без JSON, без ```). "
        f"Объём: {PREMIUM_CHAPTER_MIN_CHARS}–{PREMIUM_CHAPTER_MAX_CHARS} символов.\n"
        "Структура: ## заголовок раздела, ### подразделы, маркированные списки, "
        "пустые строки между блоками.\n"
        "Сквозной синтез: связывай параметры карты в единый психологический узор, "
        "не перечисляй изолированно.\n"
    )

    user_prompt = (
        f"Клиент: {name}\n{gender_note}\n"
        f"Задание: {brief}\n\n"
        f"Верифицированные данные карты:\n{context_block}\n\n"
        "Напиши главу premium-отчёта NeuroMule HD."
    )
    return system_prompt, user_prompt


def build_fast_facts_prompt(
    *,
    user_name: str,
    math_data: dict[str, object],
    chapter_excerpts: dict[str, str],
    user_gender: str = "",
) -> tuple[str, str]:
    """Короткий экспресс-анализ после генерации глав — только свет и якоря."""
    ctx = build_premium_context(math_data)
    excerpts = "\n".join(
        f"- {key}: {(text or '')[:400]}…"
        for key, text in chapter_excerpts.items()
        if text
    )
    system_prompt = (
        f"{_QUIET_LUXURY_TOV}\n"
        "Сгенерируй JSON: {\"fast_facts\": \"...\"}. "
        "fast_facts — 1200–1800 символов, 3 якоря силы (эмодзи ⚡💼❤️ допустимы). "
        "Только позитив и масштаб, без боли в начале."
    )
    user_prompt = (
        f"Клиент: {user_name or 'клиент'}\n"
        f"{_format_context_block(math_data, ctx)}\n\n"
        f"Фрагменты глав:\n{excerpts}\n\n"
        "Верни JSON с fast_facts."
    )
    return system_prompt, user_prompt
