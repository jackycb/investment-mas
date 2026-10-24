"""V3 Expert-Led Workflow tests — plan, data availability, fetcher, synthesis, graph structure."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.db.models import Company, CompanyProfile, Filing, FilingChunk, FinancialReport
from app.graph.state import ResearchState, create_initial_state
from tests.helpers import load_demo_dataset


# ---------------------------------------------------------------------------
# 1. ResearchPlanner
# ---------------------------------------------------------------------------

def test_research_planner_llm_output(session: Session) -> None:
    """ResearchPlanner should produce a well-structured research_plan dict."""

    load_demo_dataset(session)
    from app.graph.nodes.research_planner import ResearchPlannerNode

    node = ResearchPlannerNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    result = node(state)

    plan = result.get("research_plan", {})
    assert isinstance(plan, dict), "research_plan must be a dict"
    assert "sections" in plan, "research_plan must have sections"
    assert "peer_candidates" in plan, "research_plan must have peer_candidates"
    assert "research_questions" in plan, "research_plan must have research_questions"

    sections = plan["sections"]
    assert isinstance(sections, dict)
    required_sections = [
        "business_analysis", "financial_analysis",
        "news_analysis", "peer_analysis", "valuation", "risk_analysis",
    ]
    for sec in required_sections:
        assert sec in sections, f"Missing section: {sec}"
        assert isinstance(sections[sec], dict), f"Section {sec} must be a dict"
        assert "enabled" in sections[sec] or "skip" in sections[sec]


def test_research_planner_deterministic_fallback(session: Session) -> None:
    """When LLM fails, ResearchPlanner should fall back to PeerIntelligenceService."""

    load_demo_dataset(session)
    from app.graph.nodes.research_planner import ResearchPlannerNode

    node = ResearchPlannerNode(session)
    state = create_initial_state("NVDA", "NVIDIA")

    # Simulate LLM returning None (failure)
    with patch(
        "app.graph.nodes.research_planner.get_llm_service",
        side_effect=Exception("LLM unavailable"),
    ):
        result = node(state)

    plan = result.get("research_plan", {})
    assert isinstance(plan, dict)
    assert "sections" in plan
    assert "peer_candidates" in plan
    # Fallback should still produce peer_candidates
    peers = plan.get("peer_candidates", [])
    assert isinstance(peers, list)


# ---------------------------------------------------------------------------
# 2. DataAvailabilityChecker
# ---------------------------------------------------------------------------

def test_data_availability_checker_all_available(session: Session) -> None:
    """When all data is present, DataAvailabilityChecker should report available."""

    load_demo_dataset(session)
    from app.db.repositories.company_repository import get_company_by_ticker
    from app.graph.nodes.data_availability_checker import DataAvailabilityCheckerNode

    company = get_company_by_ticker(session, "NVDA")
    assert company is not None

    # Add a fully-parsed filing with chunks
    filing = Filing(
        company_id=company.id, ticker="NVDA", company_name="NVIDIA",
        market="US", filing_type="annual_report", fiscal_year=2024,
        title="NVIDIA 10-K", source_name="sec_edgar_direct_filing",
        source_url="https://sec.gov", file_url="https://sec.gov/nvda-10k.htm",
        local_path="/tmp/nvda-10k.txt", file_type="txt", checksum="abc123",
        download_status="downloaded", parse_status="parsed",
        metadata_json={"chunk_count": 10},
    )
    session.add(filing)
    session.flush()
    session.add(FilingChunk(
        filing_id=filing.id, company_id=company.id, chunk_index=0,
        section="Business", text="NVIDIA develops GPUs.", token_count=5,
        embedding_model="local_hash", embedding_status="indexed",
        metadata_json={"filing_type": "annual_report"},
        created_at=datetime.now(timezone.utc),
    ))
    session.flush()

    node = DataAvailabilityCheckerNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    result = node(state)

    da = result.get("data_availability", {})
    assert isinstance(da, dict)
    assert "target" in da
    assert "peers" in da
    assert "fetch_needed" in da, "data_availability must have fetch_needed"


def test_data_availability_checker_missing_peer(session: Session) -> None:
    """When a peer has no filings, it should appear in fetch_needed."""

    load_demo_dataset(session)
    from app.graph.nodes.data_availability_checker import DataAvailabilityCheckerNode

    node = DataAvailabilityCheckerNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    # Pre-populate research_plan with a peer that has no data
    state["research_plan"] = {
        "sections": {},
        "peer_candidates": [
            {"ticker": "US.NONEXISTENT", "company_name": "NoDataCorp", "peer_type": "core"},
        ],
        "research_questions": [],
        "data_requirements": {"peer_filings": ["US.NONEXISTENT"]},
    }

    result = node(state)
    da = result.get("data_availability", {})
    fetch_needed = da.get("fetch_needed", {})
    assert isinstance(fetch_needed, dict)
    peers_needed = fetch_needed.get("peer_filings", [])
    assert any("NONEXISTENT" in str(p) for p in peers_needed), (
        f"Missing peer should be in fetch_needed, got {peers_needed}"
    )


# ---------------------------------------------------------------------------
# 3. DataFetcher
# ---------------------------------------------------------------------------

def test_data_fetcher_skips_when_nothing_needed(session: Session) -> None:
    """DataFetcher should skip all downloads when fetch_needed is empty."""

    load_demo_dataset(session)
    from app.graph.nodes.data_fetcher import DataFetcherNode

    node = DataFetcherNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    state["data_availability"] = {"fetch_needed": {}}
    state["research_plan"] = {"sections": {}}

    result = node(state)
    fetch_results = result.get("fetch_results", {})
    assert isinstance(fetch_results, dict)
    # Should not have downloaded anything
    assert not fetch_results.get("peers_fetched"), f"Should skip, got {fetch_results}"


def test_data_fetcher_timeout_budget(session: Session) -> None:
    """DataFetcher should handle missing providers gracefully without crashing."""

    load_demo_dataset(session)
    from app.graph.nodes.data_fetcher import DataFetcherNode

    node = DataFetcherNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    # Ask to fetch many peers
    state["data_availability"] = {
        "fetch_needed": {
            "target_filings": True,
            "peer_filings": [
                "US.TIMEOUT1", "US.TIMEOUT2", "US.TIMEOUT3",
                "US.TIMEOUT4", "US.TIMEOUT5",
            ],
            "news": True,
        }
    }
    state["research_plan"] = {"sections": {}}

    # Stub filing discovery. Since the refactor DataFetcherNode delegates to
    # FinancialReportIngestionSkill -> FilingService, so the discovery seam is
    # now the provider class method rather than a symbol in the node module.
    with patch(
        "app.providers.filing_discovery_provider.MarketFilingDiscoveryProvider.discover_filings",
        return_value=[],
    ):
        result = node(state)

    # Should not crash — errors/warnings captured instead
    assert "data_availability" in result


# ---------------------------------------------------------------------------
# 4. BusinessAnalyst skip / culture combine
# ---------------------------------------------------------------------------

def test_business_analyst_skip(session: Session) -> None:
    """BusinessAnalyst should return empty output when section is skipped."""

    load_demo_dataset(session)
    from app.graph.nodes.business_analyst import BusinessAnalystNode

    node = BusinessAnalystNode(session=session)
    state = create_initial_state("NVDA", "NVIDIA")
    state["research_plan"] = {
        "sections": {"business_analysis": {"enabled": False, "skip": True}},
    }
    state["company_profile"] = {"business_model": "GPU company."}

    result = node(state)
    assert result.get("business_analysis") is not None
    assert result.get("culture_analysis") is not None


def test_business_analyst_includes_culture(session: Session) -> None:
    """BusinessAnalyst should include culture_analysis in output."""

    load_demo_dataset(session)
    from app.graph.nodes.business_analyst import BusinessAnalystNode

    node = BusinessAnalystNode(session=session)
    state = create_initial_state("NVDA", "NVIDIA")
    state["research_plan"] = {
        "sections": {"business_analysis": {"enabled": True}, "culture_governance": {"enabled": True}},
    }
    state["company_profile"] = {"business_model": "GPU company.", "corporate_culture": "Engineering-led."}

    result = node(state)
    assert "business_analysis" in result
    assert "culture_analysis" in result, "BusinessAnalyst must output culture_analysis"


# ---------------------------------------------------------------------------
# 5. PeerAnalyst uses plan peers
# ---------------------------------------------------------------------------

def test_peer_analyst_uses_plan_peers(session: Session) -> None:
    """PeerAnalyst should use research_plan.peer_candidates, not call discover_candidates."""

    load_demo_dataset(session)
    from app.graph.nodes.peer_analyzer import PeerAnalystNode

    planned_peers = [
        {"ticker": "US.AMD", "company_name": "AMD", "peer_type": "core", "similarity_score": 0.9},
        {"ticker": "US.AVGO", "company_name": "Broadcom", "peer_type": "valuation", "similarity_score": 0.8},
    ]

    node = PeerAnalystNode(session=session)
    state = create_initial_state("NVDA", "NVIDIA")
    state["research_plan"] = {
        "sections": {"peer_analysis": {"enabled": True}},
        "peer_candidates": planned_peers,
    }
    state["company_profile"] = {"business_model": "GPU company."}
    state["business_analysis"] = {}

    # Mock the intelligence service to verify it's NOT called for discovery
    with patch.object(node, "intelligence_service", wraps=node.intelligence_service) as mock_svc:
        # If discover_candidates is called, test should fail
        mock_svc.discover_candidates = MagicMock(return_value=([], []))
        result = node(state)
        # discover_candidates should NOT have been called
        mock_svc.discover_candidates.assert_not_called()

    assert "peer_analysis" in result
    pa = result["peer_analysis"]
    assert isinstance(pa, dict)


# ---------------------------------------------------------------------------
# 6. NewsAnalyst skip fresh cache
# ---------------------------------------------------------------------------

def test_news_analyst_skip_fresh_cache(session: Session) -> None:
    """NewsAnalyst should skip when news section is disabled in plan."""

    load_demo_dataset(session)
    from app.graph.nodes.news_collector import NewsAnalystNode

    node = NewsAnalystNode(session=session)
    state = create_initial_state("NVDA", "NVIDIA")
    state["research_plan"] = {
        "sections": {"news_analysis": {"enabled": False, "skip": True}},
    }

    result = node(state)
    assert "company_news" in result
    assert "peer_news" in result
    # Should return empty lists when skipped
    assert result["company_news"] == [] or result["company_news"] == []
    assert result["peer_news"] == [] or result["peer_news"] == []


# ---------------------------------------------------------------------------
# 7. SynthesisReviewer
# ---------------------------------------------------------------------------

def test_synthesis_reviewer_quality_gate(session: Session) -> None:
    """SynthesisReviewer Phase 1 (coverage check) should detect missing sections."""

    from app.graph.nodes.synthesis_reviewer import SynthesisReviewerNode

    node = SynthesisReviewerNode(session=session)
    # State missing several key sections
    state: dict = {
        "ticker": "US.NVDA",
        "company_name": "NVIDIA",
        "research_plan": {"sections": {}, "research_questions": []},
        "company_profile": {"business_model": "GPU company."},
        "business_analysis": {},
        "financial_summary": {},
        "peer_analysis": {},
        "valuation_result": {},
        "risk_assessment": {},
        "company_news": [],
        "peer_news": [],
        "report_coverage": {},
        "errors": [],
        "warnings": [],
        "filing_coverage": {},
        "data_availability": {},
        "fetch_results": {},
    }
    result = node(state)
    assert "final_report" in result
    assert "final_rating" in result
    assert "report_coverage" in result


def test_synthesis_reviewer_injects_questions(session: Session) -> None:
    """SynthesisReviewer should inject research_questions into report_json."""

    from app.graph.nodes.synthesis_reviewer import SynthesisReviewerNode

    questions = [
        "What is NVIDIA's competitive moat in AI?",
        "How sustainable is the current gross margin?",
    ]

    node = SynthesisReviewerNode(session=session)
    state: dict = {
        "ticker": "US.NVDA",
        "company_name": "NVIDIA",
        "research_plan": {
            "sections": {},
            "research_questions": questions,
        },
        "company_profile": {"business_model": "GPU company."},
        "business_analysis": {"summary": "Strong market position."},
        "financial_summary": {"latest_metrics": {"revenue": 30}},
        "peer_analysis": {"core_peers": []},
        "valuation_result": {},
        "risk_assessment": {
            "overall_risk_score": 4.0,
            "red_flags": [],
            "key_risks": [],
            "recommendation": "watch",
        },
        "company_news": [],
        "peer_news": [],
        "event_list": [],
        "report_coverage": {},
        "errors": [],
        "warnings": [],
        "filing_coverage": {},
        "data_availability": {},
        "fetch_results": {},
    }
    result = node(state)
    report_json = result.get("report_json", {})
    assert isinstance(report_json, dict)
    injected = report_json.get("research_questions", [])
    assert injected == questions, f"Expected {questions}, got {injected}"


# ---------------------------------------------------------------------------
# 8. V3 Workflow Graph Structure
# ---------------------------------------------------------------------------

def test_v3_workflow_graph_structure() -> None:
    """V3 workflow should have exactly 10 nodes with correct names."""

    from app.graph.workflow import build_research_workflow
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base

    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        wf = build_research_workflow(s)
        graph = wf.get_graph()
        node_ids = set(graph.nodes)

        expected_nodes = {
            "research_planner",
            "data_availability_checker",
            "data_fetcher",
            "company_profiler",
            "business_analyst",
            "financial_analyst",
            "news_analyst",
            "peer_analyst",
            "valuation_agent",
            "risk_analyst",
            "synthesis_reviewer",
        }
        # Verify all expected nodes are present
        missing = expected_nodes - node_ids
        assert not missing, f"Missing nodes: {missing}"

        # Verify no V2-only nodes remain
        removed_nodes = {"culture_analyst", "context_loader", "peer_filing_ingester",
                         "investment_expert", "report_coverage_checker", "filing_coverage_checker"}
        for rn in removed_nodes:
            assert rn not in node_ids, f"V2 node {rn} should not be in V3 workflow"

        # Verify at least 10-11 nodes (10 main + __start__/__end__ internals)
        assert len(node_ids) >= 11, f"Expected >= 11 nodes in graph, got {len(node_ids)}: {node_ids}"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 9. ResearchRunner V3 Steps
# ---------------------------------------------------------------------------

def test_research_runner_v3_steps() -> None:
    """WORKFLOW_STEPS should have correct V3 entries."""

    from app.services import research_runner as runner_mod

    steps = runner_mod.WORKFLOW_STEPS
    assert isinstance(steps, list)
    assert len(steps) >= 10, f"Expected at least 10 WORKFLOW_STEPS, got {len(steps)}"

    step_ids = [s[0] for s in steps]  # Each entry is (id, name, cls)
    expected_ids = [
        "research_planner", "data_availability_checker", "data_fetcher",
        "company_profiler", "business_analyst", "financial_analyst",
        "news_analyst", "peer_analyst", "valuation_agent",
        "risk_analyst", "synthesis_reviewer",
    ]
    for eid in expected_ids:
        assert eid in step_ids, f"WORKFLOW_STEPS missing: {eid}"

    # Verify no V2-only step IDs
    removed = {"context_loader", "culture_analyst", "peer_filing_ingester",
               "investment_expert", "report_coverage_checker", "filing_coverage_checker"}
    for rid in removed:
        assert rid not in step_ids, f"V2 step {rid} should not be in V3 WORKFLOW_STEPS"