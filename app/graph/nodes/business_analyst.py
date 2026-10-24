"""BusinessAnalyst node — V3: plan-driven with optional culture/governance absorption."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.business_analysis_service import BusinessAnalysisService
from app.services.culture_governance_service import CultureGovernanceService


class BusinessAnalystNode:
    """Analyze business model, market space, segment breakdown, and competition.

    V3 additions:
    - Reads focus_areas from the research plan to guide analysis depth.
    - Absorbs culture/governance analysis by calling CultureGovernanceService
      internally when the plan does NOT skip the culture_governance section.
    """

    def __init__(self, session: Session, service: BusinessAnalysisService | None = None, model: str | None = None) -> None:
        self.session = session
        self.service = service or BusinessAnalysisService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        plan = state.get("research_plan", {})
        sections = plan.get("sections", {})

        ba_cfg = sections.get("business_analysis", {})
        culture_cfg = sections.get("culture_governance", {})

        focus_areas = ba_cfg.get("focus_areas", []) if not ba_cfg.get("skip") else []
        culture_skipped = culture_cfg.get("skip", False)

        # 1. Business analysis
        try:
            result = self.service.analyze(
                session=self.session,
                ticker=state["ticker"],
                company_name=state["company_name"],
                company_profile=state.get("company_profile", {}),
                filing_coverage=state.get("filing_coverage", {}),
                model=self.model,
                focus_areas=focus_areas if focus_areas else None,
            )
        except Exception as exc:
            output: dict = {
                "business_analysis": {},
                "segment_analysis": {},
                "errors": [f"BusinessAnalyst failed: {exc}"],
            }
            return output

        output: dict = {
            "business_analysis": result.get("business_analysis", {}),
            "segment_analysis": result.get("segment_analysis", {}),
            "warnings": result.get("warnings", []),
        }

        # 2. Culture/governance analysis (absorbed from CultureAnalyst)
        if not culture_skipped:
            try:
                culture_result = CultureGovernanceService().analyze(
                    session=self.session,
                    ticker=state["ticker"],
                    company_name=state["company_name"],
                    company_profile=state.get("company_profile", {}),
                    filing_coverage=state.get("filing_coverage", {}),
                    model=self.model,
                )
                output["culture_analysis"] = culture_result
            except Exception as exc:
                output["culture_analysis"] = {}
                output.setdefault("warnings", []).append(f"Culture/governance analysis skipped: {exc}")

        return output