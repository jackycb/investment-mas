"""Filing-related API schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FilingSchema(BaseModel):
    """Stored filing metadata for frontend inspection."""

    id: int
    ticker: str
    company_name: str
    market: str
    filing_type: str
    fiscal_year: int | None = None
    report_date: str | None = None
    published_at: str | None = None
    title: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    file_url: str
    local_path: str | None = None
    raw_local_path: str | None = None
    file_type: str | None = None
    download_status: str
    parse_status: str
    chunk_count: int = 0
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class FilingListResponse(BaseModel):
    """Effective filing list response for a company ticker."""

    ticker: str
    company_name: str
    filings: list[FilingSchema]
