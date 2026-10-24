"""LangGraph research workflow — V3: Expert-Led Planning + Targeted Execution.

Stage 1:  ResearchPlanner          — creates research_plan via LLM
Stage 2:  DataAvailabilityChecker  — checks what data exists (no LLM)
Stage 3:  DataFetcher              — fetches/copies data for the run (no LLM)
Stage 4:  CompanyProfiler          — plan-aware profile building
Stage 5:  Parallel specialist branches (all plan-aware):
             Branch A: BusinessAnalyst (absorbs culture/governance) → PeerAnalyst
             Branch B: NewsAnalyst
             Branch C: FinancialAnalyst → ValuationAgent
Stage 6:  RiskAnalyst              — joins valuation + news + peer branches
Stage 7:  SynthesisReviewer → END

The join point moved from ValuationAgent to RiskAnalyst: ValuationAgent only
reads financial_summary / peer_comparison / company_profile, so waiting for the
business, news and peer branches added latency without adding information.
``app/services/research_runner.WORKFLOW_DAG`` is the executable form of this
graph and is kept edge-for-edge consistent with it.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.graph.nodes.business_analyst import BusinessAnalystNode
from app.graph.nodes.company_profiler import CompanyProfilerNode
from app.graph.nodes.data_availability_checker import DataAvailabilityCheckerNode
from app.graph.nodes.data_fetcher import DataFetcherNode
from app.graph.nodes.financial_analyst import FinancialAnalystNode
from app.graph.nodes.news_collector import NewsAnalystNode
from app.graph.nodes.peer_analyzer import PeerAnalystNode
from app.graph.nodes.research_planner import ResearchPlannerNode
from app.graph.nodes.risk_analyst import RiskAnalystNode
from app.graph.nodes.synthesis_reviewer import SynthesisReviewerNode
from app.graph.nodes.valuation_agent import ValuationAgentNode
from app.graph.state import ResearchState


def build_research_workflow(session: Session):
    """Build the V3 plan-driven LangGraph workflow (10 nodes).

    V3 changes from V2:
    - New stages: ResearchPlanner, DataAvailabilityChecker, DataFetcher,
      SynthesisReviewer (combines InvestmentExpert + ReportCoverageChecker)
    - BusinessAnalyst absorbs CultureAnalyst internally
    - PeerAnalyst moved to parallel branch (no longer depends on other specialists)
    - Removed: ContextLoader, FilingCoverageChecker, CultureAnalyst,
      PeerFilingIngester, InvestmentExpert, ReportCoverageChecker
    - All specialist nodes are plan-aware (skip/focus_areas)
    """

    graph = StateGraph(ResearchState)

    # ---- Stage 1-3: Plan & Data Prep ----
    graph.add_node("research_planner", ResearchPlannerNode(session))
    graph.add_node("data_availability_checker", DataAvailabilityCheckerNode(session))
    graph.add_node("data_fetcher", DataFetcherNode(session))

    # ---- Stage 4: Company Profile (plan-aware, skip_ingestion) ----
    graph.add_node("company_profiler", CompanyProfilerNode(session, skip_ingestion=True))

    # ---- Stage 5: Parallel Specialists (all plan-aware) ----
    graph.add_node("business_analyst", BusinessAnalystNode(session))
    graph.add_node("news_analyst", NewsAnalystNode(session))
    graph.add_node("financial_analyst", FinancialAnalystNode(session))
    graph.add_node("peer_analyst", PeerAnalystNode(session=session))

    # ---- Stage 6: Valuation & Risk ----
    graph.add_node("valuation_agent", ValuationAgentNode(session=session))
    graph.add_node("risk_analyst", RiskAnalystNode(session))

    # ---- Stage 7: Final Synthesis & Review ----
    graph.add_node("synthesis_reviewer", SynthesisReviewerNode(session))

    # ---- Edges ----

    # Stage 1 → 2 → 3 → 4 sequential
    graph.add_edge(START, "research_planner")
    graph.add_edge("research_planner", "data_availability_checker")
    graph.add_edge("data_availability_checker", "data_fetcher")
    graph.add_edge("data_fetcher", "company_profiler")

    # Stage 4 → 5: fan out to 4 parallel specialists
    graph.add_edge("company_profiler", "business_analyst")
    graph.add_edge("company_profiler", "news_analyst")
    graph.add_edge("company_profiler", "financial_analyst")
    graph.add_edge("company_profiler", "peer_analyst")

    # ---- Stage 5 → 6: converge to valuation_agent ----
    # Edges mirror ResearchRunner.WORKFLOW_DAG, which is derived from the state
    # fields each node actually reads:
    #   * valuation_agent consumes financial_summary / peer_comparison /
    #     company_profile only, so it must NOT wait for the business, news or
    #     peer branches — doing so lengthened the critical path for nothing.
    #   * peer_analyst consumes business_analysis, so it follows business_analyst.
    #   * risk_analyst consumes valuation_result plus event_list (news) and the
    #     peer branch, so it is the real join point.
    graph.add_edge("financial_analyst", "valuation_agent")
    graph.add_edge("valuation_agent", "risk_analyst")
    graph.add_edge("news_analyst", "risk_analyst")
    graph.add_edge("peer_analyst", "risk_analyst")

    # Stage 6 → 7 sequential
    graph.add_edge("risk_analyst", "synthesis_reviewer")
    graph.add_edge("business_analyst", "synthesis_reviewer")
    graph.add_edge("financial_analyst", "synthesis_reviewer")
    graph.add_edge("synthesis_reviewer", END)

    return graph.compile()