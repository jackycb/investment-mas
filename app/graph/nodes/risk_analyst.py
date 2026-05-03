"""RiskAnalyst node."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.repositories.company_repository import get_company_by_ticker
from app.graph.state import ResearchState
from app.services.risk_service import RiskService


class RiskAnalystNode:
    """Score investment risk with explainable rules."""

    def __init__(self, session: Session | None = None, service: RiskService | None = None) -> None:
        self.session = session
        self.service = service or RiskService()

    def __call__(self, state: ResearchState) -> dict:
        try:
            assessment = self.service.assess(
                state.get("financial_summary", {}),
                state.get("peer_comparison", {}),
                state.get("financial_red_flags", []),
                state.get("event_list", []),
                state.get("valuation_result", {}),
                retrieval_context=self._build_retrieval_context(state),
            )
        except Exception as exc:
            return {
                "risk_assessment": {},
                "errors": state.get("errors", []) + [f"RiskAnalyst failed: {exc}"],
            }
        return {"risk_assessment": assessment}

    def _build_retrieval_context(self, state: ResearchState) -> dict:
        """Build optional retrieval context without breaking existing workflow."""

        if self.session is None:
            return {}
        company = get_company_by_ticker(self.session, state.get("ticker", ""))
        if not company:
            return {}
        return {"company_id": company.id, "ticker": company.ticker}
