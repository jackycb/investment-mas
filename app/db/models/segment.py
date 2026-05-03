"""Business segment ORM models."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class CompanySegmentProfile(Base):
    """Persisted business segment analysis for group/multi-segment companies."""

    __tablename__ = "company_segment_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    report_session_id: Mapped[int | None] = mapped_column(ForeignKey("report_sessions.id", ondelete="SET NULL"), index=True)
    segment_name: Mapped[str] = mapped_column(String(255), nullable=False)
    revenue_share: Mapped[float | None] = mapped_column(Float)
    profit_share: Mapped[float | None] = mapped_column(Float)
    business_description: Mapped[str | None] = mapped_column(Text)
    market_context: Mapped[str | None] = mapped_column(Text)
    competition_context: Mapped[str | None] = mapped_column(Text)
    key_drivers: Mapped[list[str] | None] = mapped_column(JSONType)
    key_risks: Mapped[list[str] | None] = mapped_column(JSONType)
    evidence_refs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()
