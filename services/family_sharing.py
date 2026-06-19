"""
Опция DUO («Доступ на двоих») для тарифа ULTRA (1 месяц).

Владелец месячной ULTRA-подписки может привязать **одного** партнёра
(``MAX_DUO_MEMBERS = 1`` приглашённый + владелец = 2 человека).
Финансовые списания ⚡/💎 у партнёра идут с кошелька владельца; ИИ-Память,
кулдаун «Совет дня» и роли остаются индивидуальными.

Таблица ``family_members`` сохранена для обратной совместимости с БД.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import aiosqlite

from services import repository
from services.billing import store as billing_store
from services.repository import set_user_tariff

# Приглашённых партнёров (без владельца). Итого в DUO: 2 человека.
MAX_DUO_MEMBERS = 1
FAMILY_MAX_MEMBERS = MAX_DUO_MEMBERS  # deprecated alias

DUO_OWNER_PACK_TYPES: frozenset[str] = frozenset({"ULTRA_1MONTH", "ULTRA"})


async def activate_duo_owner(user_id: int) -> None:
    """Покупка ULTRA (1 месяц): пользователь становится владельцем DUO."""
    await set_user_tariff(user_id, "ULTRA")


activate_ultra_family_head = activate_duo_owner  # deprecated alias


async def has_active_duo_owner_pack(user_id: int) -> bool:
    """True, если у пользователя активен пакет ULTRA_1MONTH (или legacy ULTRA)."""
    today = date.today().isoformat()
    placeholders = ", ".join("?" for _ in DUO_OWNER_PACK_TYPES)
    types = tuple(DUO_OWNER_PACK_TYPES)
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await billing_store._ensure_balance_packages_schema(db)
        async with db.execute(
            f"""
            SELECT 1 FROM balance_packages
            WHERE user_id = ? AND package_type IN ({placeholders})
              AND (expires_at IS NULL OR expires_at >= ?)
            LIMIT 1
            """,
            (user_id, *types, today),
        ) as cur:
            row = await cur.fetchone()
    return row is not None


async def is_duo_owner_eligible(user_id: int) -> bool:
    """Владелец может управлять DUO: активный ULTRA 1 мес. + тариф ULTRA."""
    if not await has_active_duo_owner_pack(user_id):
        return False
    async with aiosqlite.connect(repository.DB_PATH) as db:
        async with db.execute(
            "SELECT tariff FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return bool(row and str(row[0] or "").upper() == "ULTRA")


async def ensure_family_schema(db: aiosqlite.Connection) -> None:
    """Создаёт таблицу family_members (DUO-пары). Безопасно для повторных вызовов."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS family_members (
            owner_id  INTEGER NOT NULL,
            member_id INTEGER NOT NULL PRIMARY KEY,
            joined_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_family_members_owner ON family_members (owner_id)"
    )


async def list_duo_partners(owner_id: int) -> list[int]:
    """Привязанные partner_id (без владельца). Порядок — по joined_at."""
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_family_schema(db)
        async with db.execute(
            "SELECT member_id FROM family_members WHERE owner_id = ? "
            "ORDER BY joined_at ASC",
            (owner_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [int(r[0]) for r in rows]


list_family_members = list_duo_partners  # deprecated alias


async def link_duo_partner(owner_id: int, member_id: int) -> tuple[bool, str]:
    """
    Привязывает партнёра к DUO владельца ``owner_id``.

    Возвращает (ok, error_key). Ошибки:
        - ``self``              — member_id == owner_id.
        - ``not_duo_eligible``  — нет активного ULTRA (1 месяц) у владельца.
        - ``already_linked``    — partner уже в другой DUO-связке.
        - ``limit_reached``     — DUO уже занята (2/2).
    """
    if owner_id == member_id:
        return False, "self"
    if not await is_duo_owner_eligible(owner_id):
        return False, "not_duo_eligible"
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_family_schema(db)
        async with db.execute(
            "SELECT owner_id FROM family_members WHERE member_id = ?",
            (member_id,),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return False, "already_linked"
        async with db.execute(
            "SELECT COUNT(*) FROM family_members WHERE owner_id = ?",
            (owner_id,),
        ) as cur:
            row = await cur.fetchone()
        if int(row[0] or 0) >= MAX_DUO_MEMBERS:
            return False, "limit_reached"
        await db.execute(
            "INSERT INTO family_members (owner_id, member_id, joined_at) "
            "VALUES (?, ?, ?)",
            (owner_id, member_id, date.today().isoformat()),
        )
        await db.commit()
    return True, ""


link_family_member = link_duo_partner  # deprecated alias


async def unlink_duo_partner(owner_id: int, member_id: int) -> bool:
    """Отвязывает партнёра. Возвращает True, если строка реально удалена."""
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_family_schema(db)
        cur = await db.execute(
            "DELETE FROM family_members WHERE owner_id = ? AND member_id = ?",
            (owner_id, member_id),
        )
        await db.commit()
        return cur.rowcount > 0


unlink_family_member = unlink_duo_partner  # deprecated alias


async def _owner_still_valid_for_duo(owner_id: int) -> bool:
    return await is_duo_owner_eligible(owner_id)


async def resolve_duo_owner(user_id: int) -> int:
    """
    Роутер кошелька DUO.

    Если ``user_id`` — привязанный партнёр и владелец всё ещё eligible,
    возвращает ``owner_id``. Иначе — сам ``user_id``.
    """
    async with aiosqlite.connect(repository.DB_PATH) as db:
        await ensure_family_schema(db)
        async with db.execute(
            "SELECT owner_id FROM family_members WHERE member_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return user_id
        owner_id = int(row[0])
        if not await _owner_still_valid_for_duo(owner_id):
            await db.execute(
                "DELETE FROM family_members WHERE member_id = ?",
                (user_id,),
            )
            await db.commit()
            return user_id
        return owner_id


resolve_family_owner = resolve_duo_owner  # deprecated alias


async def is_duo_owner(user_id: int) -> bool:
    """True, если пользователь — владелец DUO с привязанным партнёром."""
    partners = await list_duo_partners(user_id)
    return bool(partners)


is_family_owner = is_duo_owner  # deprecated alias


async def is_duo_partner(user_id: int) -> bool:
    """True, если пользователь — приглашённый партнёр в активной DUO."""
    owner_id = await resolve_duo_owner(user_id)
    return owner_id != user_id


def duo_partners_chunked(partner_ids: Iterable[int], chunk: int = 2) -> list[list[int]]:
    """Утилита для inline-клавиатур: режет id на ряды по ``chunk``."""
    ids = list(partner_ids)
    return [ids[i : i + chunk] for i in range(0, len(ids), chunk)]


family_members_chunked = duo_partners_chunked  # deprecated alias
