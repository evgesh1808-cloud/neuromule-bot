"""Сохранение CFO-отчётов в PostgreSQL (SQLAlchemy + asyncpg)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from services.db_models import Base, FinancialReport

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _to_async_dsn(dsn: str) -> str:
    raw = (dsn or "").strip()
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://") :]
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://") :]
    return raw


async def init_financial_reports_db(dsn: str) -> AsyncEngine | None:
    """Создаёт async engine и таблицу ``financial_reports`` (идемпотентно)."""
    global _engine, _session_factory
    if not dsn:
        return None
    if _engine is not None:
        return _engine

    async_dsn = _to_async_dsn(dsn)
    _engine = create_async_engine(
        async_dsn,
        pool_size=10,
        max_overflow=40,
        pool_pre_ping=True,
        pool_timeout=5.0,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("financial_reports: SQLAlchemy engine ready")
    return _engine


def get_reports_session_factory() -> async_sessionmaker[AsyncSession] | None:
    return _session_factory


async def close_financial_reports_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def _extract_shop_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    shop = metrics.get("shop")
    if isinstance(shop, dict):
        return {
            "total_revenue": float(shop.get("total_revenue") or shop.get("tax_base_revenue") or 0.0),
            "tax_total": float(shop.get("tax_total") or shop.get("tax_usn") or 0.0),
            "net_profit": float(shop.get("clear_profit") or shop.get("net_profit") or 0.0),
        }
    return {
        "total_revenue": float(metrics.get("total_revenue") or 0.0),
        "tax_total": float(metrics.get("tax_total") or 0.0),
        "net_profit": float(metrics.get("net_profit") or 0.0),
    }


async def save_user_report_to_db(user_id: int, metrics: dict[str, Any]) -> bool:
    """
    Сохраняет ``final_metrics_json`` / CFO-словарь в ``financial_reports``.

    При отсутствии PG или ошибке коммита логирует и возвращает ``False``
    (основной Excel-пайплайн не прерывается).
    """
    if not metrics or metrics.get("error"):
        return False

    factory = get_reports_session_factory()
    if factory is None:
        from config import settings

        if not (settings.postgres_dsn or "").strip():
            return False
        await init_financial_reports_db(settings.postgres_dsn)
        factory = get_reports_session_factory()
    if factory is None:
        return False

    shop_vals = _extract_shop_metrics(metrics)
    platform = str(metrics.get("platform") or "wildberries")
    tax_type = str(metrics.get("tax_type") or "USN")
    tax_rate = float(metrics.get("tax_rate") or 6.0)

    row = FinancialReport(
        user_id=int(user_id),
        platform=platform,
        tax_type=tax_type,
        tax_rate=tax_rate,
        total_revenue=shop_vals["total_revenue"],
        tax_total=shop_vals["tax_total"],
        net_profit=shop_vals["net_profit"],
        metrics_json=dict(metrics),
    )

    try:
        async with factory() as session:
            session.add(row)
            await session.commit()
        logger.info(
            "financial_reports: saved user_id=%s revenue=%.2f",
            user_id,
            shop_vals["total_revenue"],
        )
        return True
    except Exception:
        logger.exception("financial_reports: save failed user_id=%s", user_id)
        return False


__all__ = (
    "close_financial_reports_db",
    "init_financial_reports_db",
    "save_user_report_to_db",
)
