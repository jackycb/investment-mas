"""Research output ORM models."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class RiskAssessment(Base):
    """Persisted risk assessment."""

    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    fundamentals_risk_score: Mapped[float | None] = mapped_column(Float)
    competition_risk_score: Mapped[float | None] = mapped_column(Float)
    governance_risk_score: Mapped[float | None] = mapped_column(Float)
    event_risk_score: Mapped[float | None] = mapped_column(Float)
    valuation_risk_score: Mapped[float | None] = mapped_column(Float)
    overall_risk_score: Mapped[float | None] = mapped_column(Float)
    red_flags: Mapped[list[str] | None] = mapped_column(JSONType)
    key_risks: Mapped[list[str] | None] = mapped_column(JSONType)
    monitoring_signals: Mapped[list[str] | None] = mapped_column(JSONType)
    recommendation: Mapped[str | None] = mapped_column(Text)
    supporting_evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()


class ValuationResult(Base):
    """Persisted valuation result."""

    __tablename__ = "valuation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    valuation_method: Mapped[str] = mapped_column(String(64), nullable=False)
    bull_value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    base_value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    bear_value: Mapped[float | None] = mapped_column(Numeric(20, 4))
    assumptions: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    sensitivity: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    conclusion: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()


class ResearchReport(Base):
    """Persisted final research report."""

    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    executive_summary: Mapped[str | None] = mapped_column(Text)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    final_rating: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()

