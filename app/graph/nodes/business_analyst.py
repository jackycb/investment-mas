"""BusinessAnalyst node."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.business_analysis_service import BusinessAnalysisService


class BusinessAnalystNode:
    """Analyze business model, market space, segment breakdown, and competition."""

    def __init__(self, session: Session, service: BusinessAnalysisService | None = None, model: str | None = None) -> None:
        self.session = session
        self.service = service or BusinessAnalysisService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        try:
            result = self.service.analyze(
                session=self.session,
                ticker=state["ticker"],
                company_name=state["company_name"],
                company_profile=state.get("company_profile", {}),
                filing_coverage=state.get("filing_coverage", {}),
                model=self.model,
            )
        except Exception as exc:
            return {
                "business_analysis": {},
                "segment_analysis": {},
                "errors": state.get("errors", []) + [f"BusinessAnalyst failed: {exc}"],
            }
        return {
            "business_analysis": result.get("business_analysis", {}),
            "segment_analysis": result.get("segment_analysis", {}),
            "warnings": state.get("warnings", []) + result.get("warnings", []),
        }
