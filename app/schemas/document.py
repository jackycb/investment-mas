"""Document-related API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CompanyDocumentChunkSchema(BaseModel):
    """Chunk payload for one parsed company document."""

    id: int
    section: str | None = None
    page_number: int | None = None
    chunk_index: int
    text: str
    doc_type: str
    fiscal_year: int | None = None
    ticker: str
    company_name: str
    chunk_metadata: dict[str, Any] = Field(default_factory=dict)


class CompanyDocumentSchema(BaseModel):
    """Stored company document metadata with chunk count."""

    id: int
    ticker: str
    company_name: str
    doc_type: str
    fiscal_year: int | None = None
    report_date: str | None = None
    source_name: str | None = None
    source_url: str
    local_path: str
    checksum: str | None = None
    downloaded_at: str
    chunk_count: int = 0
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class CompanyDocumentListResponse(BaseModel):
    """Document list response for a company ticker."""

    ticker: str
    company_name: str
    documents: list[CompanyDocumentSchema]


class CompanyDocumentChunkListResponse(BaseModel):
    """Chunk list response for a company document."""

    ticker: str
    company_name: str
    document_id: int
    chunks: list[CompanyDocumentChunkSchema]
