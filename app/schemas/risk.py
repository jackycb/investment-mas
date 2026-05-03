"""Risk schemas."""

from pydantic import BaseModel, Field


class RiskAssessmentSchema(BaseModel):
    """Rule-based risk assessment output."""

    fundamentals_risk_score: float
    competition_risk_score: float
    governance_risk_score: float
    event_risk_score: float
    valuation_risk_score: float
    overall_risk_score: float
    red_flags: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    monitoring_signals: list[str] = Field(default_factory=list)
    recommendation: str = ""
    supporting_evidence: list[dict[str, str | float]] = Field(default_factory=list)

