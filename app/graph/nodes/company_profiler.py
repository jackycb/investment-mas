"""CompanyProfiler node."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.company_profile_service import CompanyProfileService


class CompanyProfilerNode:
    """Populate company profile and peer list."""

    def __init__(self, session: Session, service: CompanyProfileService | None = None, model: str | None = None) -> None:
        self.session = session
        self.service = service or CompanyProfileService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        try:
            profile, peers, _company = self.service.build_profile(self.session, state["ticker"], state["company_name"], model=self.model)
        except Exception as exc:
            return {
                "company_profile": {},
                "peer_list": [],
                "errors": state.get("errors", []) + [f"CompanyProfiler failed: {exc}"],
            }
        warnings = [str(item) for item in profile.get("warnings", []) if str(item).strip()]
        return {
            "company_profile": profile,
            "peer_list": peers,
            "errors": state.get("errors", []) + [f"CompanyProfiler warning: {item}" for item in warnings],
        }
