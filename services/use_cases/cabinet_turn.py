"""
Use-case: экран «👤 Мой профиль» — HTML-текст, балансы, рефералы, шкалы.

Главная сборка перенесена в ``services.use_cases.profile_view``. Этот модуль
оставлен для обратной совместимости со старыми импортами хендлеров.
"""

from __future__ import annotations

from services.use_cases.profile_view import (
    CabinetView,
    build_cabinet_view,
    build_user_profile_html,
)

__all__ = ["CabinetView", "build_cabinet_view", "build_user_profile_html"]
