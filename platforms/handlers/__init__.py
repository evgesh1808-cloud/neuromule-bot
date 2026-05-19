"""Регистрация всех Telegram-роутеров."""
from __future__ import annotations

from aiogram import Dispatcher

from platforms.handlers import (
    generation_cb,
    generation_fsm,
    hd,
    menu_support,
    payment_misc,
    start_admin,
)


def register_all(dp: Dispatcher) -> None:
    for mod in (
        start_admin,
        menu_support,
        generation_cb,
        hd,
        generation_fsm,
        payment_misc,
    ):
        dp.include_router(mod.router)
