"""Company-related API schemas."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class PeerSchema(BaseModel):
    """Simplified peer descriptor."""

    ticker: str
    company_name: str
    peer_type: str = "core"
    similarity_score: float | None = None
    similarity_dimensions: list[str] = Field(default_factory=list)
    rationale: str | None = None


class CompanyProfileSchema(BaseModel):
    """Structured company profile."""

    as_of_date: date
    business_model: str = ""
    market_space: str = ""
    competition_landscape: str = ""
    corporate_culture: str = ""
    revenue_model: str = ""
    key_products: list[str] = Field(default_factory=list)
    key_regions: list[str] = Field(default_factory=list)
    key_customers: list[str] = Field(default_factory=list)
    key_suppliers: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    management_summary: str = ""
    raw_sources: list[dict[str, Any]] = Field(default_factory=list)
