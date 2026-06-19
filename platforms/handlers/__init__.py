"""Регистрация всех Telegram-роутеров.

Порядок важен: ``start_onboarding`` подключается ПЕРВЫМ. Он перехватывает
``/start`` и ``callback_data="check_subscription"`` — это новый онбординг-флоу
(заслонка с TOS + проверка подписки + запись ``accepted_terms`` в БД).
Старый ``start_admin.start`` остаётся в реестре для обратной совместимости
(``CB_ACCEPT_LEGAL_TOS``, paywall, /admin-команды и пр.), но `/start`
до него уже не дойдёт — aiogram отдаёт событие первому матчу.
"""
from __future__ import annotations

from aiogram import Dispatcher

from platforms import music_studio
from platforms.handlers import (
    gallery_flow,
    generation_cb,
    generation_fsm,
    hd,
    inline_flow,
    memory_family,
    menu_support,
    payment_misc,
    reviews,
    start_admin,
    start_onboarding,
    table_chart_cb,
)


def register_all(dp: Dispatcher) -> None:
    for mod in (
        start_onboarding,
        start_admin,
        menu_support,
        music_studio,
        inline_flow,
        gallery_flow,
        reviews,
        generation_cb,
        table_chart_cb,
        hd,
        memory_family,
        generation_fsm,
        payment_misc,
    ):
        dp.include_router(mod.router)
