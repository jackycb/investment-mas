"""Report schemas."""

from typing import Any

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    """A reference to a piece of supporting evidence."""

    source_type: str = ""
    filing_id: int | None = None
    section: str | None = None
    page_number: int | None = None
    score: float | None = None
    snippet: str | None = None


class ReportSection(BaseModel):
    """A single section of the report with evidence tracking."""

    core_judgement: str = ""
    supporting_facts: list[str] = Field(default_factory=list)
    supporting_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class SegmentInfo(BaseModel):
    """Business segment breakdown item."""

    segment_name: str = ""
    revenue_share: float | None = None
    profit_share: float | None = None
    business_description: str = ""
    market_context: str = ""
    competition_context: str = ""
    key_drivers: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)


class SegmentAnalysisSection(BaseModel):
    """Segment analysis section for group companies."""

    is_group_company: bool = False
    segments: list[SegmentInfo] = Field(default_factory=list)
    segment_level_open_questions: list[str] = Field(default_factory=list)
    supporting_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class CompetitionAnalysisSection(BaseModel):
    """Competition analysis section."""

    core_peers: list[dict[str, Any]] = Field(default_factory=list)
    secondary_peers: list[dict[str, Any]] = Field(default_factory=list)
    valuation_peers: list[dict[str, Any]] = Field(default_factory=list)
    strengths_vs_peers: list[str] = Field(default_factory=list)
    weaknesses_vs_peers: list[str] = Field(default_factory=list)
    supporting_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class CultureGovernanceSection(BaseModel):
    """Culture and governance analysis section."""

    core_judgement: str = ""
    management_summary: str = ""
    management_stability_view: str = ""
    compensation_and_incentive_view: str = ""
    management_members: list[dict[str, Any]] = Field(default_factory=list)
    supporting_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class RiskAnalysisSection(BaseModel):
    """Risk analysis section."""

    core_judgement: str = ""
    filing_risks: list[str] = Field(default_factory=list)
    financial_risks: list[str] = Field(default_factory=list)
    governance_risks: list[str] = Field(default_factory=list)
    valuation_risks: list[str] = Field(default_factory=list)
    monitoring_signals: list[str] = Field(default_factory=list)
    what_would_change_my_mind: list[str] = Field(default_factory=list)
    supporting_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class EnhancedReportJsonSchema(BaseModel):
    """Full structured research report with evidence tracking per section."""

    company_name: str = ""
    ticker: str = ""
    executive_summary: dict[str, Any] = Field(default_factory=lambda: {
        "core_conclusion": "",
        "rating": "",
        "confidence": 0.0,
        "key_supporting_points": [],
        "key_open_questions": [],
    })
    business_model_analysis: ReportSection = Field(default_factory=ReportSection)
    segment_analysis: SegmentAnalysisSection = Field(default_factory=SegmentAnalysisSection)
    market_space_analysis: ReportSection = Field(default_factory=ReportSection)
    competition_analysis: CompetitionAnalysisSection = Field(default_factory=CompetitionAnalysisSection)
    culture_and_governance_analysis: CultureGovernanceSection = Field(default_factory=CultureGovernanceSection)
    financial_analysis: ReportSection = Field(default_factory=ReportSection)
    valuation_analysis: ReportSection = Field(default_factory=ReportSection)
    risk_analysis: RiskAnalysisSection = Field(default_factory=RiskAnalysisSection)
    final_recommendation: dict[str, Any] = Field(default_factory=lambda: {
        "final_rating": "research_more",
        "reason": "",
        "confidence": 0.0,
    })
    followup_suggestions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ReportJsonSchema(BaseModel):
    """Structured research report payload (backward compatible)."""

    executive_summary: str
    sections: dict[str, Any] = Field(default_factory=dict)
    final_rating: str
