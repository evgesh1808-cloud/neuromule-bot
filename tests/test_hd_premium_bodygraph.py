"""Premium HD bodygraph PNG overlay (Pillow)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services import hd_logic


def test_center_coordinates_cover_all_nine_centers() -> None:
    assert set(hd_logic.center_coordinates) == {
        "Голова",
        "Аджна",
        "Горло",
        "G-центр",
        "Эго",
        "Селезенка",
        "Солнечное сплетение",
        "Сакрал",
        "Корень",
    }


def test_generate_premium_bodygraph_writes_png(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hd_logic, "_HD_BODYGRAPH_OUTPUT_DIR", tmp_path)
    rel = hd_logic.generate_premium_bodygraph(["Сакрал", "Корень", "Горло"], uid=42)
    assert rel == "tmp/ready_hd_42.png"
    out = tmp_path / "ready_hd_42.png"
    assert out.is_file()
    assert out.stat().st_size > 10_000


def test_normalize_defined_center_aliases() -> None:
    names = hd_logic._normalize_defined_center_names(["сакрал", "G-центр", "джи-центр", "Горло"])
    assert names == ["Сакрал", "G-центр", "Горло"]
