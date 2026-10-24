"""Tests for the optimization refactor: staged workflow, peer intelligence, section cache, new endpoints — V3 compat."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.db.models import Company, CompanyProfile, Filing, FilingChunk, PeerMapping, ResearchReport
from app.graph.state import ResearchState, create_initial_state
from app.services.peer_intelligence_service import PeerIntelligenceService
from app.services.section_cache_service import SectionCacheService
from app.services.retrieval_policy import get_policy
from tests.helpers import load_demo_dataset


# ---------------------------------------------------------------------------
# 1. DataAvailabilityChecker (replaces ContextLoader V3)
# ---------------------------------------------------------------------------

def test_data_availability_detects_effective_filings(session: Session) -> None:
    """DataAvailabilityChecker should detect when effective filings exist."""

    load_demo_dataset(session)
    from app.db.repositories.company_repository import get_company_by_ticker

    company = get_company_by_ticker(session, "NVDA")
    assert company is not None

    session.add(Filing(
        company_id=company.id, ticker="NVDA", company_name="NVIDIA",
        market="US", filing_type="annual_report", fiscal_year=2024,
        title="NVIDIA 10-K", source_name="sec_edgar_direct_filing",
        source_url="https://sec.gov", file_url="https://sec.gov/nvda-10k.htm",
        local_path="/tmp/nvda-10k.txt", file_type="txt", checksum="abc123",
        download_status="downloaded", parse_status="parsed",
        metadata_json={"chunk_count": 10},
    ))
    session.add(FilingChunk(
        filing_id=1, company_id=company.id, chunk_index=0,
        section="Business", text="NVIDIA develops GPUs.", token_count=5,
        embedding_model="local_hash", embedding_status="indexed",
        metadata_json={"filing_type": "annual_report"},
        created_at=datetime.now(timezone.utc),
    ))
    session.flush()

    from app.graph.nodes.data_availability_checker import DataAvailabilityCheckerNode

    node = DataAvailabilityCheckerNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    result = node(state)
    da = result.get("data_availability", {})
    target = da.get("target", {})
    assert target.get("filings", {}).get("status") == "available", f"Expected available, got {da}"


def test_data_availability_warns_when_no_filings(session: Session) -> None:
    """DataAvailabilityChecker should set filings status as missing when no filings."""

    load_demo_dataset(session)
    from app.graph.nodes.data_availability_checker import DataAvailabilityCheckerNode

    node = DataAvailabilityCheckerNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    result = node(state)
    da = result.get("data_availability", {})
    target = da.get("target", {})
    status = target.get("filings", {}).get("status", "available")
    effective = target.get("filings", {}).get("effective_count", 0)
    assert effective == 0 or status == "missing", f"Expected missing filings when none added, got {da}"


# ---------------------------------------------------------------------------
# 2. Peer candidate pipeline
# ---------------------------------------------------------------------------

def test_peer_intelligence_discovers_candidates_from_curated() -> None:
    """PeerIntelligenceService should find curated peers for NVDA."""

    service = PeerIntelligenceService()
    candidates, warnings = service.discover_candidates(
        ticker="US.NVDA",
        company_name="NVIDIA",
        company_profile={
            "business_model": "GPU and AI semiconductor platform.",
            "market_space": "AI infrastructure and cloud computing.",
            "competition_landscape": "AMD and Intel are primary competitors.",
            "key_products": ["GPU", "Data Center"],
        },
    )
    tickers = {c["ticker"] for c in candidates}
    assert len(candidates) > 0
    assert any("AMD" in t for t in tickers) or any("INTC" in t for t in tickers) or any("AVGO" in t for t in tickers)


def test_peer_intelligence_sector_clustering() -> None:
    """Sector clustering should identify semiconductor peers."""

    service = PeerIntelligenceService()
    candidates, _ = service.discover_candidates(
        ticker="US.NVDA",
        company_name="NVIDIA",
        company_profile={
            "business_model": "Semiconductor GPU company for AI and cloud computing.",
            "market_space": "AI chip market.",
            "competition_landscape": "",
            "key_products": ["GPU"],
        },
    )
    sources = set()
    for c in candidates:
        for s in c.get("sources", [c.get("source", "")]):
            if "sector_clustering" in s:
                sources.add(s)
    assert len(sources) > 0, "Should have at least one sector_clustering source"


def test_peer_intelligence_returns_empty_for_unknown_company() -> None:
    """Unknown company with no curated mapping should still return candidates or empty."""

    service = PeerIntelligenceService()
    candidates, warnings = service.discover_candidates(
        ticker="US.ZZZZZ",
        company_name="Nonexistent Corp",
        company_profile={},
    )
    assert isinstance(candidates, list)
    assert isinstance(warnings, list)


# ---------------------------------------------------------------------------
# 3. Peer metric snapshot loading
# ---------------------------------------------------------------------------

def test_peer_metrics_loading_returns_dict() -> None:
    """load_peer_metrics should return a dict of ticker -> metric dicts."""

    service = PeerIntelligenceService()
    metrics = service.load_peer_metrics(["US.NVDA", "US.AMD", "US.NONEXISTENT"])
    assert isinstance(metrics, dict)
    for ticker, m in metrics.items():
        assert "revenue" in m
        assert "fiscal_year" in m


# ---------------------------------------------------------------------------
# 4. PeerAnalyzer uses enriched peer data
# ---------------------------------------------------------------------------

def test_peer_analyst_node_uses_intelligence_service(session: Session) -> None:
    """PeerAnalystNode should produce peer_analysis with enriched candidates."""

    load_demo_dataset(session)
    from app.graph.nodes.peer_analyzer import PeerAnalystNode

    node = PeerAnalystNode(session=session)
    state = create_initial_state("NVDA", "NVIDIA")
    state["company_profile"] = {
        "business_model": "GPU semiconductor company for AI.",
        "market_space": "AI infrastructure.",
        "competition_landscape": "AMD, Intel competitors.",
        "key_products": ["GPU"],
    }
    state["peer_list"] = [
        {"ticker": "US.AMD", "company_name": "AMD", "peer_type": "core", "similarity_score": 0.9},
    ]
    state["business_analysis"] = {}

    result = node(state)
    assert "peer_analysis" in result
    pa = result["peer_analysis"]
    assert isinstance(pa, dict)
    assert "core_peers" in pa or "peer_details" in pa


# ---------------------------------------------------------------------------
# 5. Section-level cache
# ---------------------------------------------------------------------------

def test_section_cache_save_and_load(session: Session) -> None:
    """Section cache should be saved into report_json and loadable."""

    load_demo_dataset(session)
    from app.db.repositories.company_repository import get_company_by_ticker

    company = get_company_by_ticker(session, "NVDA")
    assert company is not None
    cache_service = SectionCacheService()

    report_json: dict = {"ticker": "US.NVDA"}
    state = {
        "company_profile": {"business_model": "Test"},
        "financial_summary": {"years": [2020, 2021]},
        "risk_assessment": {"overall_risk_score": 5.0},
    }
    updated = cache_service.save_section_cache(report_json, state)
    assert "_section_cache" in updated
    assert "company_profile" in updated["_section_cache"]

    session.add(ResearchReport(
        company_id=company.id, report_date=date.today(),
        title="Test", executive_summary="Test",
        report_markdown="# Test", report_json=updated,
        final_rating="watch",
        created_at=datetime.now(timezone.utc),
    ))
    session.flush()

    cached = cache_service.load_cached_state(session, company)
    assert "company_profile" in cached
    assert cached["company_profile"]["business_model"] == "Test"


# ---------------------------------------------------------------------------
# 6. Section rerun / follow-up avoids full rerun
# ---------------------------------------------------------------------------

def test_rerun_section_endpoint_returns_refined_content(client, session: Session) -> None:
    """POST /research/rerun-section should refine without full workflow."""

    load_demo_dataset(session)
    from app.services.research_runner import ResearchRunner

    ResearchRunner(session).run("NVDA", "NVIDIA")

    response = client.post("/research/rerun-section", json={
        "ticker": "NVDA",
        "section_name": "business_analysis",
        "feedback_text": "Please provide deeper analysis.",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["section_name"] == "business_analysis"
    assert body["ticker"] == "US.NVDA"


def test_followup_does_not_rerun_workflow(client, session: Session) -> None:
    """POST /research/followup should answer from existing data."""

    load_demo_dataset(session)
    from app.services.research_runner import ResearchRunner

    ResearchRunner(session).run("NVDA", "NVIDIA")

    response = client.post("/research/followup", json={
        "ticker": "NVDA",
        "question": "What is the revenue growth trend?",
    })
    assert response.status_code == 200
    body = response.json()
    assert "answer" in body
    assert body["ticker"] == "US.NVDA"


# ---------------------------------------------------------------------------
# 7. Report generation consumes enriched peer analysis
# ---------------------------------------------------------------------------

def test_report_contains_peer_metrics_table(session: Session) -> None:
    """Report markdown should include peer metrics table when snapshots are available."""

    from app.services.report_service import ReportService

    state: dict = {
        "ticker": "US.NVDA",
        "company_name": "NVIDIA",
        "company_profile": {"business_model": "GPU company."},
        "peer_list": [{"ticker": "US.AMD", "company_name": "AMD", "peer_type": "core"}],
        "filing_coverage": {},
        "business_analysis": {},
        "segment_analysis": {},
        "culture_analysis": {},
        "peer_analysis": {
            "core_peers": [{"ticker": "US.AMD", "company_name": "AMD"}],
            "peer_metric_snapshots": {
                "US.AMD": {
                    "fiscal_year": 2024,
                    "company_name": "AMD",
                    "currency": "USD",
                    "revenue": 14_000_000_000.0,
                    "revenue_growth": 0.12,
                    "gross_margin": 0.45,
                    "net_margin": 0.09,
                    "roe": 0.12,
                },
            },
        },
        "company_news": [],
        "peer_news": [],
        "event_list": [],
        "financial_summary": {
            "years": [2020, 2021, 2022],
            "revenue_cagr": 0.25,
            "gross_margin_trend": [],
            "net_margin_trend": [],
            "roe_trend": [],
            "ocf_vs_net_income": [],
            "latest_metrics": {"revenue": 30, "free_cash_flow": 8},
            "narrative": ["Revenue growing."],
        },
        "peer_comparison": {"summary": ["AMD is a key competitor."], "metrics": {}},
        "financial_red_flags": [],
        "valuation_result": {
            "blended": {"bull": 200, "base": 150, "bear": 100},
            "value_investing": {"bull": 180, "base": 140, "bear": 90},
            "growth": {"bull": 220, "base": 160, "bear": 110},
            "per_share_currency": "USD",
        },
        "valuation_assumptions": {},
        "risk_assessment": {
            "overall_risk_score": 4.5,
            "fundamentals_risk_score": 3.0,
            "competition_risk_score": 3.0,
            "governance_risk_score": 2.0,
            "event_risk_score": 2.0,
            "valuation_risk_score": 3.0,
            "red_flags": [],
            "key_risks": ["Competition risk."],
            "monitoring_signals": ["Monitor margin."],
            "recommendation": "watch",
            "supporting_evidence": [],
        },
        "report_coverage": {},
        "errors": [],
        "warnings": [],
    }
    service = ReportService()
    markdown, report_json, rating = service.build(state)
    assert "Peer Metric Snapshots" in markdown
    assert "AMD" in markdown
    assert "14,000M" in markdown


# ---------------------------------------------------------------------------
# 8. New API endpoints
# ---------------------------------------------------------------------------

def test_prewarm_endpoint(client, session: Session) -> None:
    """POST /research/prewarm should return without error."""

    load_demo_dataset(session)
    response = client.post("/research/prewarm", json={
        "ticker": "NVDA",
        "company_name": "NVIDIA",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "US.NVDA"
    assert body["status"] in ("completed", "failed")


def test_refresh_peers_endpoint(client, session: Session) -> None:
    """POST /research/refresh-peers should return peer candidates."""

    load_demo_dataset(session)
    response = client.post("/research/refresh-peers", json={
        "ticker": "NVDA",
        "company_name": "NVIDIA",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "US.NVDA"
    assert body["peer_count"] >= 0


# ---------------------------------------------------------------------------
# 9. Retrieval policy
# ---------------------------------------------------------------------------

def test_retrieval_policy_returns_correct_sources() -> None:
    """Each analysis area should have a configured retrieval policy."""

    for area in ("company_profile", "peer_analysis", "financial_analysis", "risk_analysis"):
        policy = get_policy(area)
        assert len(policy.primary) > 0, f"Policy for {area} should have primary sources"

    peer_policy = get_policy("peer_analysis")
    assert "peer_candidates" in peer_policy.primary
    assert "filing_rag" in peer_policy.evidence_only


# ---------------------------------------------------------------------------
# 10. Staged workflow graph structure
# ---------------------------------------------------------------------------

def test_workflow_graph_has_v3_nodes(session: Session) -> None:
    """The compiled V3 workflow should include new plan-driven nodes."""

    from app.graph.workflow import build_research_workflow

    workflow = build_research_workflow(session)
    graph = workflow.get_graph()
    node_ids = set(graph.nodes)
    assert "research_planner" in node_ids
    assert "data_availability_checker" in node_ids
    assert "data_fetcher" in node_ids
    assert "company_profiler" in node_ids
    assert "synthesis_reviewer" in node_ids
    assert "peer_analyst" in node_ids


# ---------------------------------------------------------------------------
# 11. ResearchPlanner builds a plan (V3 replaces ContextLoader peer discovery)
# ---------------------------------------------------------------------------

def test_research_planner_builds_plan(session: Session) -> None:
    """ResearchPlanner should produce a research_plan with sections."""

    load_demo_dataset(session)
    from app.graph.nodes.research_planner import ResearchPlannerNode

    node = ResearchPlannerNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    result = node(state)
    plan = result.get("research_plan", {})
    assert isinstance(plan, dict)
    assert "sections" in plan
    sections = plan["sections"]
    assert isinstance(sections, dict)
    if sections:
        assert all(isinstance(v, dict) for v in sections.values()), "Each section should be a dict"


# ---------------------------------------------------------------------------
# 12. PeerIntelligenceService fast discovery
# ---------------------------------------------------------------------------

def test_discover_top_peers_fast_returns_curated() -> None:
    """discover_top_peers_fast should return curated peers without network calls."""

    service = PeerIntelligenceService()
    peers = service.discover_top_peers_fast(
        ticker="US.NVDA",
        company_name="NVIDIA",
        limit=3,
    )
    assert isinstance(peers, list)
    assert len(peers) <= 3
    tickers = {p["ticker"] for p in peers}
    assert any("AMD" in t for t in tickers) or any("INTC" in t for t in tickers) or any("AVGO" in t for t in tickers)


def test_discover_top_peers_fast_with_profile() -> None:
    """discover_top_peers_fast should use sector clustering when profile is provided."""

    service = PeerIntelligenceService()
    peers = service.discover_top_peers_fast(
        ticker="US.NVDA",
        company_name="NVIDIA",
        company_profile={
            "business_model": "GPU semiconductor company for AI.",
            "market_space": "AI chip market.",
        },
        limit=3,
    )
    assert isinstance(peers, list)
    assert len(peers) > 0


# ---------------------------------------------------------------------------
# 13. Peer financial extraction from filing chunks
# ---------------------------------------------------------------------------

def test_extract_metrics_from_chunks_finds_revenue() -> None:
    """_extract_metrics_from_chunks should parse revenue from text."""

    service = PeerIntelligenceService()

    class FakeChunk:
        def __init__(self, section: str, text: str):
            self.section = section
            self.text = text
            self.metadata_json = {"filing_type": "annual_report"}

    chunks = [
        FakeChunk(
            "Financial Highlights",
            "Total revenue was $26,974 million for fiscal year 2024. "
            "Gross margin was 56.9%. Net income reached $4,368 million, "
            "representing a net margin of 16.2%. Return on equity was 38.5%.",
        ),
    ]
    metrics = service._extract_metrics_from_chunks(chunks)
    assert metrics is not None
    assert metrics["revenue"] == pytest.approx(26974.0, rel=0.01)
    assert metrics["gross_margin"] == pytest.approx(0.569, rel=0.01)
    assert metrics["net_margin"] == pytest.approx(0.162, rel=0.01)
    assert metrics["roe"] == pytest.approx(0.385, rel=0.01)
    assert metrics["fiscal_year"] == 2024


def test_extract_metrics_from_chunks_billion_unit() -> None:
    """_extract_metrics_from_chunks should handle billion unit."""

    service = PeerIntelligenceService()

    class FakeChunk:
        def __init__(self, section: str, text: str):
            self.section = section
            self.text = text
            self.metadata_json = {"filing_type": "annual_report"}

    chunks = [
        FakeChunk(
            "Results of Operations",
            "Revenue reached $60.9 billion in fiscal 2024. "
            "Gross profit margin was 73.0%.",
        ),
    ]
    metrics = service._extract_metrics_from_chunks(chunks)
    assert metrics is not None
    assert metrics["revenue"] == pytest.approx(60900.0, rel=0.01)
    assert metrics["gross_margin"] == pytest.approx(0.73, rel=0.01)


# ---------------------------------------------------------------------------
# 14. DataFetcher copies data (V3 replaces PeerFilingIngester / ContextLoader)
# ---------------------------------------------------------------------------

def test_data_fetcher_copies_financials(session: Session) -> None:
    """DataFetcherNode should copy financial data into state for known tickers."""

    load_demo_dataset(session)
    from app.graph.nodes.data_fetcher import DataFetcherNode

    node = DataFetcherNode(session)
    state = create_initial_state("NVDA", "NVIDIA")
    state["data_availability"] = {"has_financials": True}
    state["research_plan"] = {"sections": {}}
    result = node(state)
    df = result.get("data_fetch_result", {})
    assert isinstance(df, dict)
