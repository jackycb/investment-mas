"""LangGraph research workflow."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.graph.nodes.business_analyst import BusinessAnalystNode
from app.graph.nodes.company_profiler import CompanyProfilerNode
from app.graph.nodes.culture_analyst import CultureAnalystNode
from app.graph.nodes.filing_coverage_checker import FilingCoverageCheckerNode
from app.graph.nodes.financial_analyst import FinancialAnalystNode
from app.graph.nodes.investment_expert import InvestmentExpertNode
from app.graph.nodes.news_collector import NewsCollectorNode
from app.graph.nodes.peer_analyzer import PeerAnalyzerNode
from app.graph.nodes.report_coverage_checker import ReportCoverageCheckerNode
from app.graph.nodes.risk_analyst import RiskAnalystNode
from app.graph.nodes.valuation_agent import ValuationAgentNode
from app.graph.state import ResearchState


def build_research_workflow(session: Session):
    """Build the enhanced sequential LangGraph workflow.

    Flow:
    CompanyProfiler -> FilingCoverageChecker -> BusinessAnalyst -> CultureAnalyst
    -> PeerAnalyzer -> NewsCollector -> FinancialAnalyst -> ValuationAgent
    -> RiskAnalyst -> InvestmentExpert -> ReportCoverageChecker -> END
    """

    graph = StateGraph(ResearchState)
    graph.add_node("company_profiler", CompanyProfilerNode(session))
    graph.add_node("filing_coverage_checker", FilingCoverageCheckerNode(session))
    graph.add_node("business_analyst", BusinessAnalystNode(session))
    graph.add_node("culture_analyst", CultureAnalystNode(session))
    graph.add_node("peer_analyzer", PeerAnalyzerNode())
    graph.add_node("news_collector", NewsCollectorNode(session))
    graph.add_node("financial_analyst", FinancialAnalystNode(session))
    graph.add_node("valuation_agent", ValuationAgentNode())
    graph.add_node("risk_analyst", RiskAnalystNode(session))
    graph.add_node("investment_expert", InvestmentExpertNode())
    graph.add_node("report_coverage_checker", ReportCoverageCheckerNode())

    graph.add_edge(START, "company_profiler")
    graph.add_edge("company_profiler", "filing_coverage_checker")
    graph.add_edge("filing_coverage_checker", "business_analyst")
    graph.add_edge("business_analyst", "culture_analyst")
    graph.add_edge("culture_analyst", "peer_analyzer")
    graph.add_edge("peer_analyzer", "news_collector")
    graph.add_edge("news_collector", "financial_analyst")
    graph.add_edge("financial_analyst", "valuation_agent")
    graph.add_edge("valuation_agent", "risk_analyst")
    graph.add_edge("risk_analyst", "investment_expert")
    graph.add_edge("investment_expert", "report_coverage_checker")
    graph.add_edge("report_coverage_checker", END)

    return graph.compile()
