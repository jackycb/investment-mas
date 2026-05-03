"""Management and incentive ORM models."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class ManagementProfile(Base):
    """Persisted management team member profiles."""

    __tablename__ = "management_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    tenure_years: Mapped[float | None] = mapped_column(Float)
    joined_date: Mapped[str | None] = mapped_column(String(32))
    background_summary: Mapped[str | None] = mapped_column(Text)
    stability_signal: Mapped[str | None] = mapped_column(String(64))
    evidence_refs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()


class IncentiveRecord(Base):
    """Persisted compensation and incentive records."""

    __tablename__ = "incentive_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    incentive_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_group: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    amount_or_ratio: Mapped[str | None] = mapped_column(String(128))
    evidence_refs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()
