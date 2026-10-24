"""FinancialAnalyst node — V3: plan-driven with skip support.

Runs through ``financial_metrics_skill`` so the deterministic indicator work is
cached and measured instead of recomputed on every run. The node's input and
output contract is unchanged: it still returns ``financial_summary``,
``peer_comparison`` and ``financial_red_flags`` produced by ``FinancialService``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.financial_service import FinancialService
from app.skills.financial_metrics import FinancialMetricsSkill

EMPTY_RESULT = {
    "financial_summary": {},
    "peer_comparison": {},
    "financial_red_flags": [],
}


class FinancialAnalystNode:
    """Analyze historical financials and peers.

    V3 additions:
    - Reads skip/focus_areas from the research plan.
    - When skip is set, returns empty financial data cleanly.
    """

    def __init__(self, session: Session, service: FinancialService | None = None) -> None:
        self.session = session
        # The skill reuses the injected service when one is supplied, so existing
        # tests that stub FinancialService keep working unchanged.
        self.skill = FinancialMetricsSkill(session=session, service=service)

    @property
    def service(self) -> FinancialService:
        """Resolved FinancialService, kept for existing introspection/tests."""

        if self.skill.service is None:
            from app.providers.financial_data_provider import build_financial_provider

            self.skill.service = FinancialService(provider=build_financial_provider(self.session))
        return self.skill.service

    def __call__(self, state: ResearchState) -> dict:
        plan = state.get("research_plan", {})
        fin_cfg = plan.get("sections", {}).get("financial_analysis", {})

        if fin_cfg.get("skip"):
            return dict(EMPTY_RESULT)

        # V3: prefer plan's peer_candidates over CompanyProfiler's peer_list
        plan_peers = plan.get("peer_candidates", [])
        peers = plan_peers if plan_peers else state.get("peer_list", [])

        try:
            metrics = self.skill.compute(
                state["ticker"],
                state["company_name"],
                peers,
                force_refresh=bool(state.get("force_refresh", False)),
            )
        except Exception as exc:
            return {**EMPTY_RESULT, "errors": [f"FinancialAnalyst failed: {exc}"]}

        result = {
            "financial_summary": metrics.financial_summary,
            "peer_comparison": metrics.peer_comparison,
            "financial_red_flags": metrics.red_flags,
            "warnings": list(metrics.warnings),
        }
        if metrics.errors:
            result["errors"] = list(metrics.errors)
        return result
