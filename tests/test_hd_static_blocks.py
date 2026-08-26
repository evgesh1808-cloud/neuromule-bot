"""Тесты статической библиотеки HD-блоков."""

from __future__ import annotations

from services import hd_static_blocks


def test_assemble_static_reference_includes_type_and_incarnation_cross() -> None:
    math_data = {
        "hd_type": "Генератор",
        "profile": "3/5",
        "authority": "Сакральный",
        "strategy": "Ждать отклик",
        "definition": "Single",
        "defined_centers": ["Сакрал"],
        "open_centers": ["Эго", "Корень"],
        "active_channels": ["20-34"],
        "key_activations": {
            "personality_sun": {"gate": 34, "line": 1},
            "personality_earth": {"gate": 20, "line": 4},
            "design_sun": {"gate": 5, "line": 2},
            "design_earth": {"gate": 15, "line": 3},
        },
        "gates": {"sun": {"gate": 34, "line": 1}, "earth": {"gate": 20, "line": 4}},
    }
    sections = hd_static_blocks.assemble_static_reference(
        math_data,
        gate_to_center={34: "Сакрал", 20: "Горло", 5: "Сакрал", 15: "G-центр"},
    )
    assert "type" in sections
    assert "Генератор" in sections["type"]
    assert "incarnation_cross" in sections
    assert "34" in sections["incarnation_cross"]
    assert "gates" not in sections
    assert "channels" in sections
    assert "20-34" in sections["channels"]


def test_format_static_reference_full_orders_sections() -> None:
    sections = {
        "type": "Тип A",
        "profile": "Профиль B",
        "incarnation_cross": "Крест C",
    }
    full = hd_static_blocks.format_static_reference_full(sections)
    assert full.index("Тип A") < full.index("Профиль B") < full.index("Крест C")


def test_profile_block_text_uses_archetype_not_raw_code() -> None:
    text = hd_static_blocks.profile_block_text("3/5")
    assert "Экспериментатор-Спасатель" in text
    assert "Профиль 3/5" not in text
    assert "3/5" not in text


def test_gate_block_text_uses_library_or_fallback() -> None:
    text = hd_static_blocks.gate_block_text(34, center="Сакрал")
    assert "34" in text
    assert "Сакрал" in text or "Power" in text or "ворот" in text.lower()
