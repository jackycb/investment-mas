"""ORM models for filing metadata and parsed filing chunks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base
from app.db.models.mixins import TimestampMixin

JSONType = JSON().with_variant(JSONB, "postgresql")


class Filing(TimestampMixin, Base):
    """Downloaded filing metadata persisted for local knowledge retrieval."""

    __tablename__ = "filings"
    __table_args__ = (UniqueConstraint("company_id", "file_url", name="uq_filing_file_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    market: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    filing_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    period_type: Mapped[str | None] = mapped_column(String(32))
    fiscal_year: Mapped[int | None] = mapped_column(Integer, index=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(32))
    report_date: Mapped[date | None] = mapped_column(Date)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(String(500))
    source_name: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str | None] = mapped_column(Text)
    file_type: Mapped[str | None] = mapped_column(String(32))
    checksum: Mapped[str | None] = mapped_column(String(128), index=True)
    file_size: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    download_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    company = relationship("Company")


class FilingChunk(Base):
    """Normalized filing chunk used for retrieval and future vector persistence."""

    __tablename__ = "filing_chunks"
    __table_args__ = (UniqueConstraint("filing_id", "chunk_index", name="uq_filing_chunk_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id", ondelete="CASCADE"), index=True, nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(255))
    page_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    embedding_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    filing = relationship("Filing")
    company = relationship("Company")
