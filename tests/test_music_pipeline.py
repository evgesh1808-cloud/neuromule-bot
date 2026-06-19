"""Тесты после фикса: доступ к музыке, брендинг, разметка чека."""

from __future__ import annotations

import pytest

from content import messages as msg
from services.billing.pricing import MUSIC_COST
from services.tariffs import TariffName, can_use_music


def test_can_use_music_free_blocked() -> None:
    assert can_use_music(TariffName.FREE) is False


@pytest.mark.parametrize(
    "tariff",
    [TariffName.MINI, TariffName.SMART, TariffName.ULTRA],
)
def test_can_use_music_allows_all_paid(tariff: TariffName) -> None:
    """ТЗ: MINI/SMART/ULTRA → pay-per-use Suno (15 💎)."""
    assert can_use_music(tariff) is True


def test_music_caption_format_html_and_receipt() -> None:
    rendered = msg.TXT_RESULT_MUSIC_CAPTION.format(
        style="lo-fi jazz",
        balance=120,
        cost=MUSIC_COST,
    )
    # Эстетичная HTML-разметка по ТЗ
    assert "<b>ТРЕК ЗАПИСАН!</b>" in rendered
    assert "📝 Стиль: lo-fi jazz" in rendered
    # Брендированный чек @NeuroMule_bot 🐎⚡️
    assert "🧾 <b>Чек операции @NeuroMule_bot 🐎⚡️:</b>" in rendered
    assert "Списано: 15 💎 (Режим: Музыка Suno AI)" in rendered
    assert "Твой остаток: 120 💎" in rendered
    # Visual separator
    assert "───────────────────" in rendered


def test_music_caption_has_no_stale_brand() -> None:
    """Старое имя «NeuroMul» (без e) не должно встречаться в новой подписи."""
    rendered = msg.TXT_RESULT_MUSIC_CAPTION.format(style="x", balance=0, cost=15)
    assert "NeuroMul " not in rendered  # без хвоста "e " → старый бренд
    assert "NeuroMule" in rendered


def test_generation_jobs_uses_new_performer() -> None:
    """Bytes-level проверка: в исходнике остался брендированный performer."""
    import pathlib

    src = pathlib.Path("services/generation_jobs.py").read_text(encoding="utf-8")
    assert 'performer="NeuroMule 🐎"' in src
    assert 'performer="NeuroMul"' not in src
