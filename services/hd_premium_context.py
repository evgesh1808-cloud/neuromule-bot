"""Premium HD chart context: PHS, Mars trauma, Dream Rave, Penta, planetary keys."""
from __future__ import annotations

from typing import Any

from services import hd_chart

MARS_TRAUMA_BY_LINE: dict[int, str] = {
    1: "Подавление",
    2: "Отрицание",
    3: "Стыд",
    4: "Отвержение",
    5: "Вина",
    6: "Разделенность",
}

PHS_DETERMINATION: dict[int, str] = {
    1: "Аппетит (Последовательность)",
    2: "Вкус",
    3: "Жажда",
    4: "Прикосновение",
    5: "Звук",
    6: "Свет",
}

PHS_ENVIRONMENT: dict[int, str] = {
    1: "Пещеры",
    2: "Рынки",
    3: "Кухни",
    4: "Горы",
    5: "Долины",
    6: "Берега",
}

PHS_MOTIVATION: dict[int, str] = {
    1: "Страх",
    2: "Надежда",
    3: "Желание",
    4: "Потребность",
    5: "Вина",
    6: "Невинность",
}

PHS_COGNITION: dict[int, str] = {
    1: "Запах",
    2: "Вкус",
    3: "Видение",
    4: "Внутреннее зрение",
    5: "Ощущение",
    6: "Прикосновение",
}

_DREAM_RAVE_CENTERS: frozenset[str] = frozenset(
    {"Голова", "Аджна", "Горло", "Солнечное сплетение", "Корень"}
)

_PENTA_GATE_NUMBERS: frozenset[int] = frozenset({1, 2, 7, 8, 10, 13, 15, 46})


def _gate_payload(gates: object, key: str) -> dict[str, Any]:
    if not isinstance(gates, dict):
        return {}
    raw = gates.get(key)
    return raw if isinstance(raw, dict) else {}


def _substructure_from_gate_payload(payload: dict[str, Any]) -> dict[str, int | float]:
    lon = payload.get("longitude")
    if isinstance(lon, (int, float)):
        return hd_chart.longitude_to_substructure(float(lon))
    gate = payload.get("gate")
    line = payload.get("line")
    if isinstance(gate, int) and isinstance(line, int):
        gate_width = 360.0 / 64.0
        line_width = gate_width / 6.0
        try:
            gate_index = hd_chart.HD_GATE_SEQUENCE.index(gate)
        except ValueError:
            gate_index = 0
        lon_approx = gate_index * gate_width + (line - 1) * line_width + line_width * 0.5
        return hd_chart.longitude_to_substructure(lon_approx)
    return {}


def build_premium_context(math_data: dict[str, object]) -> dict[str, Any]:
    """Расширенный контекст карты для Quiet Luxury premium-отчёта."""
    data = math_data if isinstance(math_data, dict) else {}
    gates = data.get("gates") if isinstance(data.get("gates"), dict) else {}
    gate_numbers = {
        int(g)
        for g in (data.get("gate_numbers") or [])
        if str(g).isdigit()
    }
    if not gate_numbers and gates:
        gate_numbers = hd_chart.collect_gate_numbers(gates)

    personality_sun = _gate_payload(gates, "sun_personality")
    design_sun = _gate_payload(gates, "sun_design")
    design_mars = _gate_payload(gates, "mars_design")
    personality_mars = _gate_payload(gates, "mars_personality")
    jupiter_p = _gate_payload(gates, "jupiter_personality")
    pluto_p = _gate_payload(gates, "pluto_personality")
    north_node = _gate_payload(gates, "moon_personality")  # fallback; true node below
    south_node = _gate_payload(gates, "moon_design")

    p_sun_sub = _substructure_from_gate_payload(personality_sun)
    d_sun_sub = _substructure_from_gate_payload(design_sun)

    mars_line = int(design_mars.get("line") or personality_mars.get("line") or 1)
    mars_line = max(1, min(6, mars_line))

    profile = str(data.get("profile") or "")
    profile_parts = profile.split("/") if profile else []
    personality_line = int(profile_parts[0]) if profile_parts and profile_parts[0].isdigit() else int(
        personality_sun.get("line") or 1
    )
    design_line = int(profile_parts[1]) if len(profile_parts) > 1 and profile_parts[1].isdigit() else int(
        design_sun.get("line") or 1
    )

    penta_gates = sorted(gate_numbers & _PENTA_GATE_NUMBERS)
    dream_gates: list[dict[str, Any]] = []
    if isinstance(gates, dict):
        for planet_key, payload in gates.items():
            if not isinstance(payload, dict):
                continue
            gate_num = payload.get("gate")
            if not isinstance(gate_num, int):
                continue
            center = hd_chart.GATE_TO_CENTER.get(gate_num, "")
            if center in _DREAM_RAVE_CENTERS:
                dream_gates.append(
                    {
                        "planet": planet_key,
                        "gate": gate_num,
                        "line": payload.get("line"),
                        "center": center,
                    }
                )

    cross_gates: list[int] = []
    for key in ("sun_personality", "earth_personality", "sun_design", "earth_design"):
        payload = _gate_payload(gates, key)
        gate = payload.get("gate")
        if isinstance(gate, int):
            cross_gates.append(gate)

    return {
        "mars_design_line": mars_line,
        "mars_trauma_label": MARS_TRAUMA_BY_LINE.get(mars_line, "Разделенность"),
        "personality_sun_color": int(p_sun_sub.get("color") or 1),
        "personality_sun_tone": int(p_sun_sub.get("tone") or 1),
        "design_sun_color": int(d_sun_sub.get("color") or 1),
        "design_sun_tone": int(d_sun_sub.get("tone") or 1),
        "phs_determination": PHS_DETERMINATION.get(int(d_sun_sub.get("color") or 1), "Свет"),
        "phs_environment_color": int(d_sun_sub.get("color") or 1),
        "phs_motivation": PHS_MOTIVATION.get(int(p_sun_sub.get("color") or 1), "Невинность"),
        "phs_cognition": PHS_COGNITION.get(int(p_sun_sub.get("tone") or 1), "Ощущение"),
        "jupiter_gate": jupiter_p.get("gate"),
        "jupiter_line": jupiter_p.get("line"),
        "pluto_gate": pluto_p.get("gate"),
        "pluto_line": pluto_p.get("line"),
        "incarnation_cross_gates": cross_gates,
        "lunar_south_gate": south_node.get("gate"),
        "lunar_north_gate": north_node.get("gate"),
        "profile_personality_line": personality_line,
        "profile_design_line": design_line,
        "profile_has_line_6": personality_line == 6 or design_line == 6,
        "penta_active_gates": penta_gates,
        "dream_rave_gates": dream_gates,
        "defined_centers": list(data.get("defined_centers") or []),
        "open_centers": list(data.get("open_centers") or []),
        "active_channels": list(data.get("active_channels") or []),
    }
