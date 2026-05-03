"""Document-related ORM models for company profiling RAG."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class CompanyDocument(Base):
    """Downloaded company document metadata."""

    __tablename__ = "company_documents"
    __table_args__ = (UniqueConstraint("company_id", "source_url", name="uq_company_document_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, index=True)
    report_date: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128), index=True)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    company = relationship("Company")


class DocumentChunk(Base):
    """Parsed chunk from a downloaded company document."""

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("company_document_id", "chunk_index", name="uq_document_chunk_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_document_id: Mapped[int] = mapped_column(
        ForeignKey("company_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, index=True)
    section: Mapped[str | None] = mapped_column(String(255))
    page_number: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company_document = relationship("CompanyDocument")
    company = relationship("Company")
