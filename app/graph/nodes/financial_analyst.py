"""FinancialAnalyst node."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.providers.financial_data_provider import build_financial_provider
from app.services.financial_service import FinancialService


class FinancialAnalystNode:
    """Analyze historical financials and peers."""

    def __init__(self, session: Session, service: FinancialService | None = None) -> None:
        self.session = session
        self.service = service or FinancialService(provider=build_financial_provider(session))

    def __call__(self, state: ResearchState) -> dict:
        try:
            financial_summary, reports, warnings = self.service.summarize_company(state["ticker"], state["company_name"])
            peer_comparison = self.service.compare_peers(
                state["ticker"], state["company_name"], reports, state.get("peer_list", [])
            )
            red_flags = self.service.identify_red_flags(financial_summary, peer_comparison)
        except Exception as exc:
            return {
                "financial_summary": {},
                "peer_comparison": {},
                "financial_red_flags": [],
                "errors": state.get("errors", []) + [f"FinancialAnalyst failed: {exc}"],
            }
        return {
            "financial_summary": financial_summary,
            "peer_comparison": peer_comparison,
            "financial_red_flags": red_flags,
            "warnings": state.get("warnings", []) + warnings,
        }
