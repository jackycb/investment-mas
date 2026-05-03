"""Financial ORM models."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class FinancialReport(Base):
    """Normalized financial report facts."""

    __tablename__ = "financial_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(16), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    revenue: Mapped[float | None] = mapped_column(Numeric(20, 4))
    gross_profit: Mapped[float | None] = mapped_column(Numeric(20, 4))
    operating_profit: Mapped[float | None] = mapped_column(Numeric(20, 4))
    net_income: Mapped[float | None] = mapped_column(Numeric(20, 4))
    operating_cash_flow: Mapped[float | None] = mapped_column(Numeric(20, 4))
    free_cash_flow: Mapped[float | None] = mapped_column(Numeric(20, 4))
    total_assets: Mapped[float | None] = mapped_column(Numeric(20, 4))
    total_liabilities: Mapped[float | None] = mapped_column(Numeric(20, 4))
    total_equity: Mapped[float | None] = mapped_column(Numeric(20, 4))
    shares_outstanding: Mapped[float | None] = mapped_column(Numeric(20, 4))
    gross_margin: Mapped[float | None] = mapped_column(Float)
    net_margin: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    roa: Mapped[float | None] = mapped_column(Float)
    debt_ratio: Mapped[float | None] = mapped_column(Float)
    raw_source: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()


class FinancialPeerSnapshot(Base):
    """Peer comparison metric snapshot."""

    __tablename__ = "financial_peer_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    company_value: Mapped[float | None] = mapped_column(Float)
    peer_avg_value: Mapped[float | None] = mapped_column(Float)
    peer_median_value: Mapped[float | None] = mapped_column(Float)
    ranking: Mapped[int | None] = mapped_column(Integer)
    percentile: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()

