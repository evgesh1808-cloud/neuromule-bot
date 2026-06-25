"""Нативная кнопка Studio (MenuButtonWebApp URL)."""

from __future__ import annotations

from platforms.telegram_studio import resolve_studio_webapp_url


def test_resolve_studio_prefers_studio_url(monkeypatch) -> None:
    from config import settings

    object.__setattr__(settings, "webapp_studio_url", "https://studio.example/app/")
    object.__setattr__(settings, "webapp_shop_url", "https://shop.example/")
    assert resolve_studio_webapp_url() == "https://studio.example/app/"


def test_resolve_studio_falls_back_to_table_base(monkeypatch) -> None:
    from config import settings

    object.__setattr__(settings, "webapp_studio_url", None)
    object.__setattr__(settings, "webapp_shop_url", None)
    object.__setattr__(
        settings,
        "webapp_table_reports_url",
        "https://user.github.io/neuromule-table/?report_id={report_id}",
    )
    assert resolve_studio_webapp_url() == "https://user.github.io/neuromule-table/"
