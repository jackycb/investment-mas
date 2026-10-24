"""Research workflow request and response schemas."""

from typing import Any

from pydantic import BaseModel


class ResearchRunRequest(BaseModel):
    """Run research request."""

    ticker: str
    company_name: str
    model: str | None = None
    #: Bypass section-cache reuse and computation caches for this run (§6.5).
    #: Defaults to False so existing clients keep the current behaviour.
    force_refresh: bool = False


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


class PrewarmRequest(BaseModel):
    """Trigger background filing ingestion."""

    ticker: str
    company_name: str
    peers: list[str] = []


class PrewarmResponse(BaseModel):
    """Prewarm result."""

    ticker: str
    filings_ingested: int = 0
    warnings: list[str] = []
    status: str = "completed"


class RefreshPeersRequest(BaseModel):
    """Refresh peer candidates and metrics."""

    ticker: str
    company_name: str


class RefreshPeersResponse(BaseModel):
    """Refresh peers result."""

    ticker: str
    peer_count: int = 0
    peers_with_metrics: int = 0
    peer_candidates: list[dict[str, Any]] = []
    warnings: list[str] = []


class RerunSectionRequest(BaseModel):
    """Rerun a single report section."""

    ticker: str
    section_name: str
    feedback_text: str = ""


class RerunSectionResponse(BaseModel):
    """Section rerun result."""

    ticker: str
    section_name: str
    refined_content: str = ""
    impacted_sections: list[str] = []


class PerformanceRunSummary(BaseModel):
    """One persisted research run (optimization task §10 telemetry).

    Every field is measured from ``node_executions`` rows; nothing is estimated.
    """

    task_id: str
    ticker: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    total_duration_ms: float | None = None
    node_count: int = 0
    serial_sum_ms: float = 0.0
    parallelism: dict[str, Any] = {}
    slowest_nodes: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    llm_call_count: int = 0
    total_tokens: int = 0
    cache_hits: int = 0
    retry_count: int = 0
    failed_nodes: list[str] = []
    skipped_nodes: list[str] = []
    error_types: list[str] = []


class PerformanceHistoryResponse(BaseModel):
    """Per-run performance history plus a repeat-run comparison."""

    ticker: str
    run_count: int = 0
    runs: list[PerformanceRunSummary] = []
    #: ``None`` until at least two runs exist for the ticker.
    comparison: dict[str, Any] | None = None


class RunPerformanceDetailResponse(PerformanceRunSummary):
    """Full span-level detail for a single run."""

    spans: list[dict[str, Any]] = []
