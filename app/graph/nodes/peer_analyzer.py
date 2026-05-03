"""PeerAnalyzer node."""

from __future__ import annotations

from app.graph.state import ResearchState
from app.services.peer_analysis_service import PeerAnalysisService


class PeerAnalyzerNode:
    """Classify and explain peer relationships based on evidence."""

    def __init__(self, service: PeerAnalysisService | None = None, model: str | None = None) -> None:
        self.service = service or PeerAnalysisService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        try:
            result = self.service.analyze(
                ticker=state["ticker"],
                company_name=state["company_name"],
                company_profile=state.get("company_profile", {}),
                peer_list=state.get("peer_list", []),
                business_analysis=state.get("business_analysis", {}),
                model=self.model,
            )
        except Exception as exc:
            return {
                "peer_analysis": {},
                "errors": state.get("errors", []) + [f"PeerAnalyzer failed: {exc}"],
            }
        return {
            "peer_analysis": result,
        }
