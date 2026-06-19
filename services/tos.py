"""TOS-gate сервис: фиксируем безоговорочное принятие оферты, политики
конфиденциальности и условий регулярных платежей при первом ``/start``.

Под капотом используем существующее поле ``users.accepted_terms`` (INTEGER
0/1), чтобы не дублировать миграцию. Семантически в продукте оно называется
``is_tos_accepted`` — для краткости и единообразия с ТЗ NeuroMule 🐎⚡️.

Контракт:

* :func:`is_tos_accepted(user_id)` — ``True``, если флаг выставлен в 1.
  Для несуществующего пользователя возвращает ``False`` (значит, его сначала
  нужно прогнать через ``/start``-gate).
* :func:`accept_tos(user_id)` — атомарно ставит флаг в ``True``.

Перед обоими вызовами гарантируется существование пользователя в БД через
``repository.ensure_user``.
"""

from __future__ import annotations

import logging

from services import repository

logger = logging.getLogger(__name__)


async def is_tos_accepted(user_id: int) -> bool:
    """Возвращает ``True``, если пользователь принял ОПФ / Политику / Подписку."""

    return await repository.user_has_accepted_terms(int(user_id))


async def accept_tos(user_id: int) -> None:
    """Атомарно фиксирует факт принятия TOS пользователем.

    Идемпотентно: повторный вызов на уже принявшем юзере не делает ничего
    вредного (тот же ``UPDATE accepted_terms = 1``). Также гарантирует, что
    запись о пользователе уже существует в ``users``.
    """

    await repository.ensure_user(int(user_id))
    await repository.set_user_accepted_terms(int(user_id), accepted=True)
    logger.info("tos: accepted user_id=%s", user_id)


__all__ = ("is_tos_accepted", "accept_tos")
