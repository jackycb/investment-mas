"""CultureAnalyst node."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.culture_governance_service import CultureGovernanceService


class CultureAnalystNode:
    """Analyze corporate culture, management, compensation, and governance."""

    def __init__(self, session: Session, service: CultureGovernanceService | None = None, model: str | None = None) -> None:
        self.session = session
        self.service = service or CultureGovernanceService()
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
                "culture_analysis": {},
                "errors": state.get("errors", []) + [f"CultureAnalyst failed: {exc}"],
            }
        return {
            "culture_analysis": result,
            "warnings": state.get("warnings", []) + result.get("warnings", []),
        }
