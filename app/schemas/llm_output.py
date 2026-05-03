"""Pydantic schemas for structured LLM outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _BaseOutputModel(BaseModel):
    """Shared config for LLM output schemas."""

    model_config = ConfigDict(extra="ignore")


class CompanyProfileLLMOutput(_BaseOutputModel):
    """Validated company profile payload returned by the LLM."""

    company_name: str
    ticker: str
    business_model: str
    revenue_model: str
    key_products: list[str]
    key_customers: list[str]
    key_regions: list[str]
    market_space: str
    competition_landscape: str
    competitive_advantages: list[str]
    competitive_weaknesses: list[str]
    risk_factors: list[str]
    corporate_culture: str
    management_summary: str
    confidence: float = Field(ge=0.0, le=1.0)


class PeerAnalysisItem(_BaseOutputModel):
    """Single peer explanation record."""

    ticker: str
    company_name: str
    peer_type: Literal["core", "secondary", "valuation", "weakly_related"]
    similarity_dimensions: list[
        Literal["product", "customer", "industry", "technology", "business_model", "region", "valuation_comparable"]
    ]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class PeerExplanationLLMOutput(_BaseOutputModel):
    """Validated peer explanation payload returned by the LLM."""

    company_name: str
    ticker: str
    peer_analysis: list[PeerAnalysisItem]


class NewsEventItem(_BaseOutputModel):
    """Single event extracted from one news article."""

    event_type: Literal[
        "guidance_cut",
        "guidance_raise",
        "earnings_beat",
        "earnings_miss",
        "product_launch",
        "product_delay",
        "management_change",
        "lawsuit",
        "regulatory_action",
        "investigation",
        "margin_pressure",
        "demand_slowdown",
        "supply_chain_issue",
        "customer_win",
        "customer_loss",
        "capex_increase",
        "financing_activity",
        "acquisition",
        "partnership",
        "other",
    ]
    sentiment: Literal["positive", "neutral", "negative", "mixed"]
    importance_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    risk_tags: list[str]
    impact_dimensions: list[str]
    event_summary: str
    evidence_span: str


class NewsAnalysisItem(_BaseOutputModel):
    """News analysis result for one article."""

    news_id: str
    title: str
    summary_cn: str
    events: list[NewsEventItem]

    @model_validator(mode="before")
    @classmethod
    def normalize_summary_alias(cls, data: object) -> object:
        """Accept common LLM alias ``summary`` and normalize it to ``summary_cn``.

        The prompt asks for ``summary_cn``, but some providers occasionally
        return ``summary`` while still providing valid Chinese content. Treating
        that as a recoverable alias keeps news analysis from failing the entire
        research run for a harmless field-name drift.
        """

        if isinstance(data, dict) and not data.get("summary_cn") and data.get("summary"):
            return {**data, "summary_cn": data.get("summary")}
        return data


class NewsAnalysisLLMOutput(_BaseOutputModel):
    """Validated news analysis payload returned by the LLM."""

    company_name: str
    ticker: str
    news_analysis: list[NewsAnalysisItem]


class ReportSectionBase(_BaseOutputModel):
    """Base section with common fields."""

    core_judgement: str
    open_questions: list[str] = Field(default_factory=list)


class BusinessAnalysisSection(ReportSectionBase):
    """Business analysis section."""

    supporting_facts: list[str] = Field(default_factory=list)


class FinancialAnalysisSection(ReportSectionBase):
    """Financial analysis section."""

    supporting_facts: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class PeerComparisonAnalysisSection(ReportSectionBase):
    """Peer comparison section."""

    strengths_vs_peers: list[str] = Field(default_factory=list)
    weaknesses_vs_peers: list[str] = Field(default_factory=list)


class ValuationAnalysisSection(_BaseOutputModel):
    """Valuation section."""

    core_judgement: str
    preferred_method: Literal["value", "growth", "mixed"]
    bull_base_bear_summary: str
    key_assumptions: list[str] = Field(default_factory=list)
    valuation_risks: list[str] = Field(default_factory=list)


class RiskReviewSection(_BaseOutputModel):
    """Risk review section."""

    core_judgement: str
    key_risks: list[str] = Field(default_factory=list)
    monitoring_signals: list[str] = Field(default_factory=list)
    what_would_change_my_mind: list[str] = Field(default_factory=list)


class FinalRecommendationSection(_BaseOutputModel):
    """Final recommendation section."""

    final_rating: Literal["buy", "watch", "avoid", "research_more"]
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class NewsDigestItem(_BaseOutputModel):
    """One summarized news item."""

    headline: str
    summary: str
    sentiment: Literal["positive", "negative", "neutral"] = "neutral"
    relevance: str = ""


class InvestmentReportLLMOutput(_BaseOutputModel):
    """Validated final investment report payload returned by the LLM."""

    company_name: str
    ticker: str
    executive_summary: str
    business_analysis: BusinessAnalysisSection
    financial_analysis: FinancialAnalysisSection
    peer_comparison_analysis: PeerComparisonAnalysisSection
    valuation_analysis: ValuationAnalysisSection
    risk_review: RiskReviewSection
    news_digest: list[NewsDigestItem] = Field(default_factory=list)
    final_recommendation: FinalRecommendationSection
