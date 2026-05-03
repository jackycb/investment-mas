"""InvestmentExpert node."""

from __future__ import annotations

from app.graph.state import ResearchState
from app.services.report_service import ReportService


class InvestmentExpertNode:
    """Produce the final research report and investment rating."""

    def __init__(self, service: ReportService | None = None, model: str | None = None) -> None:
        self.service = service or ReportService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        try:
            report_markdown, report_json, final_rating = self.service.build(state, model=self.model)
        except Exception as exc:
            return {
                "final_report": "",
                "final_rating": "research_more",
                "report_json": {},
                "errors": state.get("errors", []) + [f"InvestmentExpert failed: {exc}"],
            }
        return {"final_report": report_markdown, "final_rating": final_rating, "report_json": report_json}
