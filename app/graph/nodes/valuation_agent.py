"""ValuationAgent node."""

from __future__ import annotations

from app.graph.state import ResearchState
from app.services.valuation_service import ValuationService


class ValuationAgentNode:
    """Generate valuation scenarios."""

    def __init__(self, service: ValuationService | None = None) -> None:
        self.service = service or ValuationService()

    def __call__(self, state: ResearchState) -> dict:
        try:
            result, assumptions = self.service.run(
                state.get("financial_summary", {}),
                state.get("peer_comparison", {}),
                state.get("company_profile", {}),
                ticker=state.get("ticker"),
                company_name=state.get("company_name"),
            )
        except Exception as exc:
            return {
                "valuation_result": {},
                "valuation_assumptions": {},
                "errors": state.get("errors", []) + [f"ValuationAgent failed: {exc}"],
            }
        return {"valuation_result": result, "valuation_assumptions": assumptions}
