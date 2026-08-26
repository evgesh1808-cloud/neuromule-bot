"""Human Design chart math: timezone-aware ephemeris, design arc, channels, profile."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.hd_profile_archetypes import (
    HD_PROFILE_ARCHETYPES,
    normalize_profile_code,
    profile_archetype_label,
)

try:
    import swisseph as swe
except ImportError:  # pragma: no cover
    swe = None

logger = logging.getLogger(__name__)

HD_DESIGN_SOLAR_ARC_DEG = 88.0

HD_GATE_SEQUENCE: tuple[int, ...] = (
    25, 17, 21, 51, 42, 3, 27, 24, 2, 23, 8, 20, 16, 35, 45, 12, 15, 52, 39, 53,
    62, 56, 31, 33, 7, 4, 29, 59, 40, 64, 47, 6, 46, 18, 48, 57, 32, 50, 28, 44,
    1, 43, 14, 34, 9, 5, 26, 11, 10, 58, 38, 54, 61, 60, 41, 19, 13, 49, 30, 55,
    37, 63, 22, 36,
)

CHANNELS_MAP: tuple[tuple[int, int], ...] = (
    (1, 8),
    (2, 14),
    (3, 60),
    (4, 63),
    (5, 15),
    (6, 59),
    (7, 31),
    (9, 52),
    (10, 20),
    (10, 34),
    (10, 57),
    (11, 56),
    (12, 22),
    (13, 33),
    (16, 48),
    (17, 62),
    (18, 58),
    (19, 49),
    (20, 34),
    (20, 57),
    (21, 45),
    (23, 43),
    (24, 61),
    (25, 51),
    (26, 44),
    (27, 50),
    (28, 38),
    (29, 46),
    (30, 41),
    (32, 54),
    (34, 57),
    (35, 36),
    (37, 40),
    (39, 55),
    (42, 53),
    (47, 64),
)

GATE_TO_CENTER: dict[int, str] = {
    64: "Голова",
    61: "Голова",
    63: "Голова",
    47: "Аджна",
    24: "Аджна",
    4: "Аджна",
    17: "Аджна",
    43: "Аджна",
    11: "Аджна",
    62: "Горло",
    23: "Горло",
    56: "Горло",
    35: "Горло",
    12: "Горло",
    45: "Горло",
    33: "Горло",
    31: "Горло",
    8: "Горло",
    20: "Горло",
    16: "Горло",
    1: "G-центр",
    13: "G-центр",
    25: "G-центр",
    46: "G-центр",
    2: "G-центр",
    15: "G-центр",
    10: "G-центр",
    7: "G-центр",
    21: "Эго",
    51: "Эго",
    26: "Эго",
    40: "Эго",
    48: "Селезенка",
    57: "Селезенка",
    44: "Селезенка",
    50: "Селезенка",
    32: "Селезенка",
    28: "Селезенка",
    18: "Селезенка",
    5: "Сакрал",
    14: "Сакрал",
    29: "Сакрал",
    59: "Сакрал",
    9: "Сакрал",
    34: "Сакрал",
    27: "Сакрал",
    42: "Сакрал",
    3: "Сакрал",
    6: "Солнечное сплетение",
    37: "Солнечное сплетение",
    22: "Солнечное сплетение",
    36: "Солнечное сплетение",
    30: "Солнечное сплетение",
    55: "Солнечное сплетение",
    49: "Солнечное сплетение",
    53: "Корень",
    60: "Корень",
    52: "Корень",
    19: "Корень",
    39: "Корень",
    41: "Корень",
    58: "Корень",
    38: "Корень",
    54: "Корень",
}

ALL_CENTER_NAMES: tuple[str, ...] = (
    "Голова",
    "Аджна",
    "Горло",
    "G-центр",
    "Эго",
    "Сакрал",
    "Селезенка",
    "Солнечное сплетение",
    "Корень",
)

MOTOR_CENTERS: frozenset[str] = frozenset({"Сакрал", "Эго", "Корень", "Солнечное сплетение"})

HD_TYPE_STRATEGIES: dict[str, str] = {
    "генератор": "Ждать отклик и отвечать телом",
    "манифестирующий генератор": "Ждать отклик, затем информировать и действовать",
    "манифестор": "Информировать окружающих перед действием",
    "проектор": "Ждать приглашения и признания",
    "рефлектор": "Ждать лунный цикл (28 дней) для важных решений",
}

_PROFILE_FALLBACK_LABELS: dict[str, str] = {
    "5/5": "Двойной Спасатель (Линия 5/5 — проекция)",
}

_BIRTH_NUMBERS_RE = re.compile(
    r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})(?:\D+(\d{1,2})[:.](\d{2}))?"
)

_CITY_TZ_ALIASES: dict[str, str] = {
    "москва": "Europe/Moscow",
    "moscow": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow",
    "петербург": "Europe/Moscow",
    "spb": "Europe/Moscow",
    "saint petersburg": "Europe/Moscow",
    "чебоксары": "Europe/Moscow",
    "cheboksary": "Europe/Moscow",
    "казань": "Europe/Moscow",
    "kazan": "Europe/Moscow",
    "новосибирск": "Asia/Novosibirsk",
    "екатеринбург": "Asia/Yekaterinburg",
    "красноярск": "Asia/Krasnoyarsk",
    "владивосток": "Asia/Vladivostok",
    "киев": "Europe/Kyiv",
    "kyiv": "Europe/Kyiv",
    "минск": "Europe/Minsk",
    "алматы": "Asia/Almaty",
    "астана": "Asia/Almaty",
    "тбилиси": "Asia/Tbilisi",
    "ереван": "Asia/Yerevan",
    "баку": "Asia/Baku",
    "ташкент": "Asia/Tashkent",
    "берlin": "Europe/Berlin",
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "new york": "America/New_York",
}

_PLANET_BODIES: tuple[tuple[str, int], ...] = (
    ("sun", swe.SUN if swe else 0),
    ("moon", swe.MOON if swe else 1),
    ("mercury", swe.MERCURY if swe else 2),
    ("venus", swe.VENUS if swe else 3),
    ("mars", swe.MARS if swe else 4),
    ("jupiter", swe.JUPITER if swe else 5),
    ("saturn", swe.SATURN if swe else 6),
    ("uranus", swe.URANUS if swe else 7),
    ("neptune", swe.NEPTUNE if swe else 8),
    ("pluto", swe.PLUTO if swe else 9),
)


def require_swe():
    if swe is None:
        raise RuntimeError("pyswisseph не установлен")
    return swe


def extract_birth_numbers(raw: str) -> tuple[int, int, int, int, int] | None:
    match = _BIRTH_NUMBERS_RE.search(raw or "")
    if not match:
        return None
    day, month, year = (int(match.group(i)) for i in (1, 2, 3))
    hour = int(match.group(4) or 12)
    minute = int(match.group(5) or 0)
    return year, month, day, hour, minute


def extract_birth_place(raw: str) -> str:
    body = _BIRTH_NUMBERS_RE.sub(" ", raw or "", count=1)
    cleaned = re.sub(r"\b(?:город|г\.)\s*", " ", body, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;")
    return cleaned


def resolve_city_timezone(city: str) -> str:
    key = (city or "").strip().lower()
    if not key:
        return "Europe/Moscow"
    if key in _CITY_TZ_ALIASES:
        return _CITY_TZ_ALIASES[key]
    for alias, tz_name in _CITY_TZ_ALIASES.items():
        if alias in key or key in alias:
            return tz_name
    if re.search(r"[а-яё]", key):
        return "Europe/Moscow"
    return "UTC"


def local_birth_to_utc_jd(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    *,
    tz_name: str,
) -> tuple[float, datetime, datetime]:
    sw = require_swe()
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %s, fallback Europe/Moscow", tz_name)
        tz = ZoneInfo("Europe/Moscow")
    local_dt = datetime(year, month, day, hour, minute, tzinfo=tz)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    utc_hour = utc_dt.hour + utc_dt.minute / 60.0 + utc_dt.second / 3600.0
    jd = sw.julday(utc_dt.year, utc_dt.month, utc_dt.day, utc_hour)
    return jd, local_dt, utc_dt


def longitude_to_gate(longitude: float) -> dict[str, int | float]:
    gate_width = 360.0 / 64.0
    line_width = gate_width / 6.0
    normalized = longitude % 360.0
    gate_index = int(normalized // gate_width)
    position_in_gate = normalized - gate_index * gate_width
    line = int(position_in_gate // line_width) + 1
    return {
        "gate": HD_GATE_SEQUENCE[gate_index],
        "line": min(line, 6),
        "longitude": round(normalized, 6),
    }


def sun_longitude(jd: float) -> float:
    sw = require_swe()
    pos, _flags = sw.calc_ut(jd, sw.SUN)
    return float(pos[0])


def find_design_jd(personality_jd: float) -> float:
    """Design moment: Sun exactly HD_DESIGN_SOLAR_ARC_DEG before personality Sun (Newton-Raphson)."""
    target = (sun_longitude(personality_jd) - HD_DESIGN_SOLAR_ARC_DEG) % 360.0
    jd = personality_jd - 88.0
    for _ in range(48):
        lon = sun_longitude(jd)
        err = (lon - target + 180.0) % 360.0 - 180.0
        if abs(err) < 1e-9:
            return jd
        lon_next = sun_longitude(jd + 0.01)
        deriv = ((lon_next - lon + 180.0) % 360.0 - 180.0) / 0.01
        if abs(deriv) < 1e-12:
            deriv = 0.985647
        jd -= err / deriv
    return jd


def format_hd_channel(g1: int, g2: int) -> str:
    low, high = sorted((g1, g2))
    return f"{low}-{high}"


def derive_active_channels(gate_numbers: set[int]) -> list[str]:
    if not gate_numbers:
        return []
    active: list[str] = []
    seen: set[str] = set()
    for g1, g2 in CHANNELS_MAP:
        if g1 in gate_numbers and g2 in gate_numbers:
            label = format_hd_channel(g1, g2)
            if label not in seen:
                seen.add(label)
                active.append(label)
    return sorted(active)


def derive_defined_centers_from_gates(gate_numbers: set[int]) -> set[str]:
    defined: set[str] = set()
    for ch in derive_active_channels(gate_numbers):
        g1, g2 = (int(part) for part in ch.split("-", 1))
        for gate in (g1, g2):
            center = GATE_TO_CENTER.get(gate)
            if center:
                defined.add(center)
    return defined


def infer_hd_type_from_centers(defined: set[str]) -> str:
    if not defined:
        return "Рефлектор"
    motors_throat = {"Эго", "Солнечное сплетение", "Корень"}
    if "Сакрал" in defined:
        if "Горло" in defined and defined & motors_throat:
            return "Манифестирующий Генератор"
        return "Генератор"
    if "Горло" in defined and defined & motors_throat:
        return "Манифестор"
    return "Проектор"


def infer_authority_from_centers(defined: set[str]) -> str:
    if not defined:
        return "Лунный"
    if "Солнечное сплетение" in defined:
        return "Эмоциональный"
    if "Сакрал" in defined:
        return "Сакральный"
    if "Селезенка" in defined:
        return "Селезеночный"
    if "Эго" in defined:
        return "Эго"
    if "G-центр" in defined:
        return "Самопроецируемый"
    return "Внутренний (уточняется по каналам)"


def strategy_for_hd_type(hd_type: str) -> str:
    key = (hd_type or "").strip().lower()
    for pattern, strategy in HD_TYPE_STRATEGIES.items():
        if pattern in key:
            return strategy
    return "Следовать стратегии своего типа"


def derive_definition_type(defined_centers: set[str], active_channels: list[str]) -> str:
    if not defined_centers:
        return "None"
    adjacency: dict[str, set[str]] = {center: set() for center in defined_centers}
    for ch in active_channels:
        parts = ch.split("-", 1)
        if len(parts) != 2:
            continue
        g1, g2 = int(parts[0]), int(parts[1])
        c1 = GATE_TO_CENTER.get(g1)
        c2 = GATE_TO_CENTER.get(g2)
        if c1 in defined_centers and c2 in defined_centers:
            adjacency[c1].add(c2)
            adjacency[c2].add(c1)
    visited: set[str] = set()
    components = 0
    for center in defined_centers:
        if center in visited:
            continue
        components += 1
        stack = [center]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(adjacency[node] - visited)
    if components <= 1:
        return "Single"
    if components == 2:
        return "Split"
    if components == 3:
        return "Triple"
    return "Quad"


def resolve_profile_archetype(personality_line: int, design_line: int) -> tuple[str, str]:
    profile = f"{personality_line}/{design_line}"
    code = normalize_profile_code(profile)
    if code in HD_PROFILE_ARCHETYPES:
        return code, HD_PROFILE_ARCHETYPES[code]
    if code in _PROFILE_FALLBACK_LABELS:
        return code, _PROFILE_FALLBACK_LABELS[code]
    label = profile_archetype_label(code)
    return code, label


def _calc_side_gates(jd: float) -> dict[str, dict[str, int | float]]:
    sw = require_swe()
    gates: dict[str, dict[str, int | float]] = {}
    for name, body in _PLANET_BODIES:
        pos, _flags = sw.calc_ut(jd, body)
        gates[name] = longitude_to_gate(float(pos[0]))
    sun_lon = float(sw.calc_ut(jd, sw.SUN)[0][0])
    gates["earth"] = longitude_to_gate((sun_lon + 180.0) % 360.0)
    return gates


def _merge_gate_dicts(
    personality: dict[str, dict[str, int | float]],
    design: dict[str, dict[str, int | float]],
) -> dict[str, dict[str, int | float]]:
    merged: dict[str, dict[str, int | float]] = {}
    for name, payload in personality.items():
        merged[f"{name}_personality"] = payload
    for name, payload in design.items():
        merged[f"{name}_design"] = payload
    return merged


def collect_gate_numbers(gates: object) -> set[int]:
    nums: set[int] = set()
    if not isinstance(gates, dict):
        return nums
    for payload in gates.values():
        if isinstance(payload, dict):
            gate = payload.get("gate")
            if isinstance(gate, int):
                nums.add(gate)
    return nums


def build_pure_hd_chart(birth_data: str) -> dict[str, Any]:
    """
    Полный IHDS-расчёт: local time → UTC, design −88° Sun (Newton-Raphson),
    personality + design, каналы, центры, профиль.
    """
    parts = extract_birth_numbers(birth_data)
    if parts is None:
        raise ValueError("Не удалось найти дату рождения в формате ДД.ММ.ГГГГ и время ЧЧ:ММ.")
    year, month, day, hour, minute = parts
    city = extract_birth_place(birth_data)
    tz_name = resolve_city_timezone(city)
    personality_jd, local_dt, utc_dt = local_birth_to_utc_jd(
        year, month, day, hour, minute, tz_name=tz_name
    )
    design_jd = find_design_jd(personality_jd)

    personality_gates = _calc_side_gates(personality_jd)
    design_gates = _calc_side_gates(design_jd)
    merged_gates = _merge_gate_dicts(personality_gates, design_gates)
    gate_numbers = collect_gate_numbers(merged_gates)

    active_channels = derive_active_channels(gate_numbers)
    defined_set = derive_defined_centers_from_gates(gate_numbers)
    open_centers = [name for name in ALL_CENTER_NAMES if name not in defined_set]
    hd_type = infer_hd_type_from_centers(defined_set)
    authority = infer_authority_from_centers(defined_set)
    strategy = strategy_for_hd_type(hd_type)
    definition = derive_definition_type(defined_set, active_channels)

    p_line = int(personality_gates["sun"]["line"])
    d_line = int(design_gates["sun"]["line"])
    profile, profile_archetype = resolve_profile_archetype(p_line, d_line)

    key_activations = {
        "personality_sun": personality_gates["sun"],
        "personality_earth": personality_gates["earth"],
        "design_sun": design_gates["sun"],
        "design_earth": design_gates["earth"],
    }

    return {
        "birth_data": birth_data.strip(),
        "birth_place": city,
        "timezone": tz_name,
        "birth_local": local_dt.isoformat(),
        "birth_utc": utc_dt.isoformat(),
        "personality_jd": personality_jd,
        "design_jd": design_jd,
        "personality_gates": personality_gates,
        "design_gates": design_gates,
        "gates": merged_gates,
        "gate_numbers": sorted(gate_numbers),
        "active_channels": active_channels,
        "defined_centers": sorted(defined_set),
        "open_centers": open_centers,
        "hd_type": hd_type,
        "profile": profile,
        "profile_archetype": profile_archetype,
        "authority": authority,
        "strategy": strategy,
        "definition": definition,
        "key_activations": key_activations,
    }


def build_domain_synthesis_pairs(math_data: dict[str, object]) -> dict[str, dict[str, object]]:
    """
    ANTI-REPEAT: одна уникальная open×motor пара на домен.
    Деньги → Эго+Сакрал; Отношения → Солнечное сплетение; Энергия → Split + канал 10-34.
    """
    defined_raw = math_data.get("defined_centers") or []
    open_raw = math_data.get("open_centers") or []
    channels_raw = math_data.get("active_channels") or []
    defined = {str(item) for item in defined_raw if str(item).strip()}
    open_centers = [str(item) for item in open_raw if str(item).strip()]
    channels = [str(item).strip() for item in channels_raw if str(item).strip()]
    motors = sorted(defined & MOTOR_CENTERS)
    definition = str(math_data.get("definition") or "").strip()

    def _pair(open_center: str, extra_anchors: list[str], channel_hints: list[str]) -> dict[str, object]:
        anchors = list(motors)
        anchors.extend(extra_anchors)
        if channel_hints:
            anchors.extend(f"канал {hint}" for hint in channel_hints)
        if not anchors:
            anchors = ["определённые моторы отсутствуют — опирайся только на факты карты"]
        return {
            "open_center": open_center,
            "anchors": anchors,
            "channel_hints": channel_hints,
        }

    sacral_anchor = ["опора: определённый Сакрал"] if "Сакрал" in defined else []
    split_anchor = (
        [f"тип определённости: {definition}"]
        if definition.lower() in {"split", "triple", "quad"}
        else []
    )
    channel_10_34 = [ch for ch in channels if ch.replace(" ", "") in {"10-34", "34-10"}]

    money_open = "Эго" if "Эго" in open_centers else (open_centers[0] if open_centers else "Эго")
    love_open = (
        "Солнечное сплетение"
        if "Солнечное сплетение" in open_centers
        else next((c for c in open_centers if c != money_open), "Солнечное сплетение")
    )
    energy_open = next((c for c in open_centers if c not in {money_open, love_open}), love_open)

    return {
        "money": _pair(money_open, sacral_anchor, []),
        "love": _pair(love_open, [], []),
        "energy": _pair(
            energy_open,
            split_anchor,
            channel_10_34 or ([channels[0]] if channels else []),
        ),
    }
