"""ResearchPlanner node — Stage 1 LLM planning step."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.graph.state import ResearchState
from app.services.aliyun_llm_service import get_llm_service
from app.services.peer_intelligence_service import PeerIntelligenceService

logger = get_logger(__name__)

PLANNER_SYSTEM_PROMPT = """You are a senior investment research planner. Given a company ticker and name, create a focused research plan.

Output valid JSON with these exact fields:

{
  "sections": {
    "business_analysis": {"priority": "high|medium|low", "skip": false, "depth": "quick|standard|deep", "focus_areas": ["area1", "area2"]},
    "culture_governance": {"priority": "high|medium|low", "skip": false, "depth": "quick|standard|deep", "focus_areas": []},
    "peer_analysis": {"priority": "high|medium|low", "skip": false, "depth": "quick|standard|deep", "focus_areas": []},
    "financial_analysis": {"priority": "high|medium|low", "skip": false, "depth": "quick|standard|deep", "focus_areas": []},
    "valuation": {"priority": "high|medium|low", "skip": false, "depth": "quick|standard|deep", "focus_areas": []},
    "risk_analysis": {"priority": "high|medium|low", "skip": false, "depth": "quick|standard|deep", "focus_areas": []},
    "news_analysis": {"priority": "high|medium|low", "skip": false, "depth": "quick|standard|deep", "focus_areas": []}
  },
  "peer_candidates": [
    {"ticker": "US.META", "company_name": "Meta Platforms", "rationale": "Direct competitor in social media and advertising"}
  ],
  "data_requirements": {
    "target_filing": true,
    "target_financials": true,
    "peer_filings": ["US.META"],
    "peer_financials": ["US.META"],
    "recent_news": true,
    "company_basic_info": true
  },
  "research_questions": ["What is the company's competitive moat?"]
}

Rules:
- "skip": true only for truly irrelevant sections (e.g., news_analysis for a stable utility, culture_governance for a widely-held ETF)
- "depth": "quick" for simple well-known businesses, "deep" for complex conglomerates or companies in transition, "standard" otherwise
- "focus_areas": specific topics to emphasize within each section, from: ["moat", "growth_drivers", "margin_trends", "capital_allocation", "management_track_record", "industry_position", "regulatory_risk", "technology_risk", "competitive_threats", "financial_quality", "valuation_multiple", "earnings_quality", "sentiment", "catalyst_events", "balance_sheet_strength", "cash_flow_quality", "peer_positioning"]
- "peer_candidates": 1-3 closest DIRECT industry peers (same sector and similar business model) with market-prefix tickers (HK.xxxxx, US.XXXX, CN.xxxxxx), brief rationale. NEVER include the target company itself as a peer. Only include companies that are genuine competitors or comparable businesses — never include companies from unrelated industries
- "research_questions": 3-5 critical questions the report must answer

Respond with valid JSON only. No markdown formatting, no code fences."""


class ResearchPlannerNode:
    """Generate a focused research plan using LLM reasoning.

    This runs as Stage 1 after ContextLoader. The plan guides downstream
    specialists on what to focus on and what to skip.
    """

    def __init__(self, session: Session, model: str | None = None) -> None:
        self.session = session
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        ticker = state["ticker"]
        company_name = state["company_name"] or ticker

        plan = self._plan_with_llm(ticker, company_name)
        if plan is None:
            plan = self._deterministic_fallback(ticker, company_name)

        return {"research_plan": plan}

    def _plan_with_llm(self, ticker: str, company_name: str) -> dict | None:
        """Use LLM to reason about the best research plan."""
        try:
            llm = get_llm_service()
        except Exception:
            logger.warning("research_planner_llm_unavailable", ticker=ticker)
            return None

        if not llm.available:
            logger.warning("research_planner_llm_unavailable", ticker=ticker)
            return None

        curated_hints = self._get_curated_peer_hints(ticker, company_name)
        user_message = f"Company: {company_name} (Ticker: {ticker}). Create a research plan."
        if curated_hints:
            hint_lines = ", ".join(f"{p['ticker']} ({p['company_name']})" for p in curated_hints)
            user_message += f"\nSuggested peers (use these if appropriate): {hint_lines}"
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        try:
            response = llm.chat(messages=messages, temperature=0.3, model=self.model)
            if response is None:
                return None
            content = str(response)
            # Strip markdown code fences if present
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:]) if len(lines) > 1 else content
            if content.endswith("```"):
                content = content.rsplit("```", 1)[0].strip()

            plan = json.loads(content)

            # Validate required keys
            if "sections" not in plan or "peer_candidates" not in plan:
                logger.warning("research_planner_missing_keys", ticker=ticker, keys=list(plan.keys()))
                return None

            # Ensure all sections exist with defaults
            default_sections = [
                "business_analysis", "culture_governance", "peer_analysis",
                "financial_analysis", "valuation", "risk_analysis", "news_analysis",
            ]
            for section_name in default_sections:
                if section_name not in plan.get("sections", {}):
                    plan["sections"][section_name] = {
                        "priority": "high", "skip": False, "depth": "standard", "focus_areas": [],
                    }

            # Ensure data_requirements exists
            if "data_requirements" not in plan:
                plan["data_requirements"] = {
                    "target_filing": True, "target_financials": True,
                    "peer_filings": [], "peer_financials": [],
                    "recent_news": True, "company_basic_info": True,
                }

            if "research_questions" not in plan:
                plan["research_questions"] = []

            # Cap peers at 3, filter out entries missing required fields,
            # and exclude the target company itself
            raw_peers = plan.get("peer_candidates", [])
            ticker_upper = ticker.upper()
            valid_peers = [
                p for p in raw_peers
                if isinstance(p, dict) and p.get("ticker") and p.get("company_name")
                and str(p["ticker"]).upper() != ticker_upper
            ][:3]
            plan["peer_candidates"] = valid_peers

            logger.info("research_planner_llm_success", ticker=ticker,
                         peer_count=len(valid_peers),
                         question_count=len(plan.get("research_questions", [])))
            return plan

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("research_planner_llm_parse_failed", ticker=ticker, error=str(exc))
            return None
        except Exception as exc:
            logger.error("research_planner_llm_error", ticker=ticker, error=str(exc))
            return None

    def _get_curated_peer_hints(self, ticker: str, company_name: str) -> list[dict]:
        """Return curated peer candidates to guide the LLM planner."""
        try:
            peer_service = PeerIntelligenceService()
            return peer_service.discover_top_peers_fast(
                ticker=ticker, company_name=company_name, limit=3,
            )
        except Exception:
            return []

    def _deterministic_fallback(self, ticker: str, company_name: str) -> dict:
        """Use PeerIntelligenceService and defaults when LLM is unavailable."""
        logger.info("research_planner_deterministic_fallback", ticker=ticker)

        try:
            peer_service = PeerIntelligenceService()
            peers = peer_service.discover_top_peers_fast(
                ticker=ticker, company_name=company_name, limit=3,
            )
        except Exception:
            peers = []

        peer_candidates = [
            {
                "ticker": p.get("ticker", ""),
                "company_name": p.get("company_name", ""),
                "rationale": p.get("rationale", "Industry peer"),
            }
            for p in peers
        ]

        return {
            "sections": {
                "business_analysis": {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []},
                "culture_governance": {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []},
                "peer_analysis": {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []},
                "financial_analysis": {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []},
                "valuation": {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []},
                "risk_analysis": {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []},
                "news_analysis": {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []},
            },
            "peer_candidates": peer_candidates,
            "data_requirements": {
                "target_filing": True,
                "target_financials": True,
                "peer_filings": [p["ticker"] for p in peer_candidates],
                "peer_financials": [p["ticker"] for p in peer_candidates],
                "recent_news": True,
                "company_basic_info": True,
            },
            "research_questions": [],
        }