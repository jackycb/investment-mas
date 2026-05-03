"""Company-related ORM models."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base
from app.db.models.mixins import TimestampMixin

JSONType = JSON().with_variant(JSONB, "postgresql")


class Company(TimestampMixin, Base):
    """Master company entity."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(64))
    market: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(16))
    country: Mapped[str | None] = mapped_column(String(64))
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    sub_industry: Mapped[str | None] = mapped_column(String(128))
    official_website: Mapped[str | None] = mapped_column(String(255))
    ir_website: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)


class CompanyProfile(Base):
    """Company qualitative research profile."""

    __tablename__ = "company_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    business_model: Mapped[str | None] = mapped_column(Text)
    market_space: Mapped[str | None] = mapped_column(Text)
    competition_landscape: Mapped[str | None] = mapped_column(Text)
    corporate_culture: Mapped[str | None] = mapped_column(Text)
    revenue_model: Mapped[str | None] = mapped_column(Text)
    key_products: Mapped[list[str] | None] = mapped_column(JSONType)
    key_regions: Mapped[list[str] | None] = mapped_column(JSONType)
    key_customers: Mapped[list[str] | None] = mapped_column(JSONType)
    key_suppliers: Mapped[list[str] | None] = mapped_column(JSONType)
    risk_factors: Mapped[list[str] | None] = mapped_column(JSONType)
    management_summary: Mapped[str | None] = mapped_column(Text)
    raw_sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()


class PeerMapping(Base):
    """Peer mapping relationships between companies."""

    __tablename__ = "peer_mappings"
    __table_args__ = (UniqueConstraint("company_id", "peer_company_id", "peer_type", name="uq_peer_mapping"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    peer_company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    peer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    similarity_score: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship(foreign_keys=[company_id])
    peer_company: Mapped["Company"] = relationship(foreign_keys=[peer_company_id])
