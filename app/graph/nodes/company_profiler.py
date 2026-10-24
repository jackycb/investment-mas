"""CompanyProfiler node — V3: plan-aware with skip_ingestion from research plan."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.company_profile_service import CompanyProfileService


class CompanyProfilerNode:
    """Populate company profile and peer list.

    V3 additions:
    - Reads company_profile section from the research plan.
    - When the plan says skip, avoids ingestion even if constructor permits it.
    - Constructor skip_ingestion remains as a hard override for demo/CLI use.
    """

    def __init__(self, session: Session, service: CompanyProfileService | None = None, model: str | None = None, skip_ingestion: bool = False) -> None:
        self.session = session
        self.service = service or CompanyProfileService()
        self.model = model
        self.skip_ingestion = skip_ingestion

    def __call__(self, state: ResearchState) -> dict:
        skip = self.skip_ingestion

        # Plan-driven override: if plan says skip company_profile, enforce skip
        plan = state.get("research_plan", {})
        profile_cfg = plan.get("sections", {}).get("company_profile", {})
        if profile_cfg.get("skip"):
            skip = True

        ctx = state.get("_context", {})
        if ctx.get("has_effective_filings"):
            skip = True

        focus_areas = profile_cfg.get("focus_areas", [])

        try:
            profile, peers, _company = self.service.build_profile(self.session, state["ticker"], state["company_name"], model=self.model, skip_ingestion=skip)
        except Exception as exc:
            return {
                "company_profile": {},
                "peer_list": [],
                "errors": [f"CompanyProfiler failed: {exc}"],
            }
        warnings_list = [str(item) for item in profile.get("warnings", []) if str(item).strip()]
        return {
            "company_profile": profile,
            "peer_list": peers,
            "errors": [f"CompanyProfiler warning: {item}" for item in warnings_list],
        }
