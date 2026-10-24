"""ValuationAgent node — V3: plan-driven with skip support.

Delegates to ``valuation_skill``, which wraps the existing Python valuation
program. **No valuation formula lives in this node or in the skill** — buy and
sell prices are exactly what ``ValuationService.run`` (and therefore the bundled
``run_value_analysis_by_code.py`` / ``value_investment_market_value.py``) already
produced. The skill only adds input validation, caching and run telemetry.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.valuation_service import ValuationService
from app.skills.valuation import ValuationSkill

EMPTY_RESULT = {"valuation_result": {}, "valuation_assumptions": {}}


class ValuationAgentNode:
    """Generate valuation scenarios.

    V3 additions:
    - Reads skip/focus_areas from the research plan.
    - When skip is set, returns empty valuation data cleanly.
    """

    def __init__(self, service: ValuationService | None = None, session: Session | None = None) -> None:
        self.session = session
        self.skill = ValuationSkill(service=service, session=session)

    @property
    def service(self) -> ValuationService:
        """Resolved ValuationService, kept for existing introspection/tests."""

        return self.skill.service

    def __call__(self, state: ResearchState) -> dict:
        plan = state.get("research_plan", {})
        val_cfg = plan.get("sections", {}).get("valuation", {})

        if val_cfg.get("skip"):
            return dict(EMPTY_RESULT)

        try:
            result = self.skill.run(
                state.get("financial_summary", {}),
                state.get("peer_comparison", {}),
                state.get("company_profile", {}),
                ticker=state.get("ticker"),
                company_name=state.get("company_name"),
                force_refresh=bool(state.get("force_refresh", False)),
            )
        except Exception as exc:
            return {**EMPTY_RESULT, "errors": [f"ValuationAgent failed: {exc}"]}

        patch = result.as_state_patch()
        if result.errors:
            patch["errors"] = list(result.errors)
        if result.warnings:
            patch["warnings"] = list(result.warnings)
        return patch
