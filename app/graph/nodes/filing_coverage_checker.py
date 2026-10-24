"""FilingCoverageChecker node."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.filing_coverage_service import FilingCoverageService


class FilingCoverageCheckerNode:
    """Check whether key topics have sufficient filing evidence before downstream analysis."""

    def __init__(self, session: Session, service: FilingCoverageService | None = None) -> None:
        self.session = session
        self.service = service or FilingCoverageService()

    def __call__(self, state: ResearchState) -> dict:
        try:
            coverage = self.service.check(
                self.session,
                state["ticker"],
                state.get("company_profile", {}),
            )
        except Exception as exc:
            return {
                "filing_coverage": {},
                "errors": [f"FilingCoverageChecker failed: {exc}"],
            }
        warnings = coverage.get("warnings", [])
        return {
            "filing_coverage": coverage,
            "warnings": warnings,
        }
