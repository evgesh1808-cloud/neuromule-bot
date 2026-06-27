"""SQLAlchemy-модели PostgreSQL (Highload CFO v12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс декларативных моделей."""


class FinancialReport(Base):
    """История финансовых отчётов пользователя (JSONB ~2 КБ на строку)."""

    __tablename__ = "financial_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="wildberries")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False, default="USN")
    tax_rate: Mapped[float] = mapped_column(Float, nullable=False, default=6.0)
    total_revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_profit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_financial_reports_user_id_created_at", "user_id", "created_at"),
    )


__all__ = ("Base", "FinancialReport")
