"""CultureAnalyst node — V3: plan-driven with skip support."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.culture_governance_service import CultureGovernanceService


class CultureAnalystNode:
    """Analyze corporate culture, management, compensation, and governance.

    V3 additions:
    - Reads skip/focus_areas from the research plan.
    - When skip is set, returns empty culture_analysis cleanly.
    - May be bypassed entirely when BusinessAnalyst absorbs this analysis.
    """

    def __init__(self, session: Session, service: CultureGovernanceService | None = None, model: str | None = None) -> None:
        self.session = session
        self.service = service or CultureGovernanceService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        plan = state.get("research_plan", {})
        culture_cfg = plan.get("sections", {}).get("culture_governance", {})

        if culture_cfg.get("skip"):
            return {"culture_analysis": {}}

        focus_areas = culture_cfg.get("focus_areas", [])

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
                "errors": [f"CultureAnalyst failed: {exc}"],
            }
        return {
            "culture_analysis": result,
            "warnings": result.get("warnings", []),
        }
