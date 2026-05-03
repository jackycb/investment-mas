"""Research workflow request and response schemas."""

from typing import Any

from pydantic import BaseModel


class ResearchRunRequest(BaseModel):
    """Run research request."""

    ticker: str
    company_name: str
    model: str | None = None


class ResearchRunResponse(BaseModel):
    """Run research response."""

    ticker: str
    company_name: str
    final_rating: str
    executive_summary: str
    report_markdown: str
    report_json: dict[str, Any]


class LatestResearchResponse(ResearchRunResponse):
    """Latest report response."""


class ResearchChatRequest(BaseModel):
    """Chat request grounded in the latest research report."""

    ticker: str
    message: str


class ResearchChatResponse(BaseModel):
    """Chat response grounded in the latest research report."""

    ticker: str
    answer: str


class FollowupQARequest(BaseModel):
    """Follow-up question request with evidence-grounded answering."""

    ticker: str
    question: str


class FollowupQAResponse(BaseModel):
    """Follow-up question response with evidence refs."""

    ticker: str
    answer: str
    evidence_refs: list[dict[str, Any]] = []
    answer_confidence: float = 0.0
    unresolved_questions: list[str] = []


class FeedbackRequest(BaseModel):
    """User feedback for section-level refinement."""

    ticker: str
    section_name: str
    feedback_type: str = "too_shallow"
    feedback_text: str = ""


class FeedbackResponse(BaseModel):
    """Feedback refinement response."""

    ticker: str
    updated_section: str
    impacted_sections: list[str] = []
    refined_content: str = ""
    additional_evidence_count: int = 0
    feedback_resolution: str = ""
