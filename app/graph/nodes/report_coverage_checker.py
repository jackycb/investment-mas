"""ReportCoverageChecker node."""

from __future__ import annotations

from app.graph.state import ResearchState
from app.services.report_coverage_service import ReportCoverageService


class ReportCoverageCheckerNode:
    """Post-report quality gate: check evidence sufficiency and flag gaps."""

    def __init__(self, service: ReportCoverageService | None = None) -> None:
        self.service = service or ReportCoverageService()

    def __call__(self, state: ResearchState) -> dict:
        try:
            result = self.service.check(state)
        except Exception as exc:
            return {
                "report_coverage": {},
                "errors": state.get("errors", []) + [f"ReportCoverageChecker failed: {exc}"],
            }
        warnings = result.get("warnings", [])
        return {
            "report_coverage": result,
            "warnings": state.get("warnings", []) + warnings,
        }
