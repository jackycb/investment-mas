# V3 Implementation Plan: Expert-Led Planning + Targeted Execution

> Target executor: DeepSeek v4 Pro / Claude Code / Codex
> Reference: `docs/investment_mas_system_architecture.md` sections 5.1, 6, 12, 14

---

## Overview

Refactor the LangGraph research workflow from **v2 Staged Parallel** (13 nodes, 4 parallel branches) to **v3 Expert-Led Planning + Targeted Execution** (10 nodes, 4 parallel specialist branches).

Core change: let an LLM (ResearchPlanner) decide WHAT to research before the system executes HOW.

### Current v2 DAG (to be replaced)
```
START → context_loader → company_profiler → filing_coverage_checker
  → [business_analyst → culture_analyst] | [news_collector] | [financial_analyst] | [peer_filing_ingester]
  → peer_analyzer → valuation_agent → risk_analyst → investment_expert → report_coverage_checker → END
```

### Target v3 DAG
```
START → research_planner → data_availability_checker → data_fetcher → company_profiler
  → [business_analyst] | [financial_analyst] | [news_analyst] | [peer_analyst]
  → valuation_agent → risk_analyst → synthesis_reviewer → END
```

---

## Phase 1: New Nodes (ResearchPlanner, DataAvailabilityChecker, DataFetcher)

### 1.1 ResearchState changes

**File**: `app/graph/state.py`

**Already done** — the following fields already exist in `ResearchState` (lines 26-28) and
`create_initial_state()` (with empty-dict defaults):

```python
research_plan: dict           # ResearchPlanner output
data_availability: dict       # DataAvailabilityChecker output
fetch_results: dict           # DataFetcher output
```

No changes needed in `state.py`.

### 1.2 ResearchPlanner node

**New file**: `app/graph/nodes/research_planner.py`

```python
class ResearchPlannerNode:
    def __init__(self, session: Session, model: str | None = None) -> None:
        self.session = session
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        ticker = state["ticker"]
        company_name = state["company_name"]

        # Try LLM planning first
        plan = self._plan_with_llm(ticker, company_name)

        # Fallback to deterministic plan if LLM fails
        if plan is None:
            plan = self._deterministic_fallback(ticker, company_name)

        return {"research_plan": plan}
```

**LLM prompt** — use the prompt from architecture doc section 5.1.4. Output JSON with:
- `sections`: dict of section_name → `{priority, focus_areas, skip, depth}`
  - Sections: `business_analysis`, `culture_governance`, `peer_analysis`, `financial_analysis`, `valuation`, `risk_analysis`, `news_analysis`
- `peer_candidates`: list of `{ticker, company_name, rationale}` — top 3 peers, market-prefix format
- `data_requirements`: `{target_filing, target_financials, peer_filings: [tickers], peer_financials: [tickers], recent_news, company_basic_info}`
- `research_questions`: 3-5 key questions

**Deterministic fallback** (when LLM unavailable):
```python
def _deterministic_fallback(self, ticker, company_name):
    from app.services.peer_intelligence_service import PeerIntelligenceService
    peers = PeerIntelligenceService().discover_top_peers_fast(ticker, company_name, limit=3)
    return {
        "sections": {s: {"priority": "high", "skip": False, "depth": "standard", "focus_areas": []}
                     for s in ["business_analysis", "culture_governance", "peer_analysis",
                               "financial_analysis", "valuation", "risk_analysis", "news_analysis"]},
        "peer_candidates": peers,
        "data_requirements": {
            "target_filing": True, "target_financials": True,
            "peer_filings": [p["ticker"] for p in peers],
            "peer_financials": [p["ticker"] for p in peers],
            "recent_news": True, "company_basic_info": True,
        },
        "research_questions": [],
    }
```

**LLM service call**: Use `app/services/aliyun_llm_service.py` `get_llm_service()`.
- For JSON output, call `llm.chat_json(messages=[...])` which returns `dict | None` directly.
- Alternatively call `llm.chat(messages=[...])` which returns `str | None` (not an object with `.content`), then parse with `json.loads()`.
- If parsing/call fails, use deterministic fallback.

### 1.3 DataAvailabilityChecker node

**New file**: `app/graph/nodes/data_availability_checker.py`

No LLM. Purely deterministic DB/filesystem queries.

```python
class DataAvailabilityCheckerNode:
    def __init__(self, session: Session) -> None:
        self.session = session

    def __call__(self, state: ResearchState) -> dict:
        plan = state.get("research_plan", {})
        ticker = state["ticker"]
        company_name = state["company_name"]

        # 1. Get or create company
        company = get_or_create_company(self.session, ticker, company_name)

        # 2. Check target company data
        target = self._check_target(company)

        # 3. Check each peer from plan
        peers = {}
        for peer in plan.get("peer_candidates", []):
            peers[peer["ticker"]] = self._check_peer(peer["ticker"])

        # 4. Check news freshness
        news = self._check_news(company)

        # 5. Check section cache
        section_cache = self._check_section_cache(company)

        # 6. Compute fetch_needed
        fetch_needed = self._compute_fetch_needed(plan, target, peers, news)

        availability = {
            "company_id": company.id,
            "target": target,
            "peers": peers,
            "news": news,
            "section_cache": section_cache,
            "fetch_needed": fetch_needed,
        }

        # Also populate filing_coverage for backward compat
        filing_coverage = self._build_filing_coverage(target)

        return {
            "data_availability": availability,
            "filing_coverage": filing_coverage,
            "_context": {
                "company_id": company.id,
                "has_effective_filings": target["filings"]["status"] == "available",
                "effective_filing_count": target["filings"].get("effective_count", 0),
                "top_peers": plan.get("peer_candidates", []),
                # ... other fields from old ContextLoader
            },
        }
```

**Reuse existing logic from**:
- `ContextLoaderNode.__call__()` — company lookup, filing check, cached sections, peer info → migrate into `_check_target()`
- `FilingCoverageCheckerNode.__call__()` → `FilingCoverageService.check()` → migrate into `_build_filing_coverage()`
- `SectionCacheService.load_cached_state()` → call in `_check_section_cache()`

**Output format**: Match the JSON format in architecture doc section 5.1.5.

### 1.4 DataFetcher node

**New file**: `app/graph/nodes/data_fetcher.py`

No LLM. Only downloads what `fetch_needed` specifies.

```python
class DataFetcherNode:
    def __init__(self, session: Session, filing_service: FilingService | None = None) -> None:
        self.session = session
        self.filing_service = filing_service or FilingService()

    def __call__(self, state: ResearchState) -> dict:
        availability = state.get("data_availability", {})
        fetch_needed = availability.get("fetch_needed", {})
        plan = state.get("research_plan", {})
        warnings = []

        fetch_results = {"target_filings": False, "peer_filings": {}, "news": False}

        # 1. Target filing
        if fetch_needed.get("target_filings"):
            # Reuse PeerFilingIngesterNode logic but for target company
            company = get_or_create_company(self.session, state["ticker"], state["company_name"])
            market = infer_market_from_ticker(state["ticker"])
            filings, w = self.filing_service.ingest_company_filings(
                session=self.session, company=company,
                market=market, years=1, filing_types={"annual_report"},
            )
            warnings.extend(w)
            fetch_results["target_filings"] = len(filings) > 0

        # 2. Peer filings
        for peer_ticker in fetch_needed.get("peer_filings", []):
            # Reuse logic from PeerFilingIngesterNode.__call__()
            # ...
            fetch_results["peer_filings"][peer_ticker] = success

        # 3. News prefetch flag
        if fetch_needed.get("news"):
            fetch_results["news"] = True  # actual fetch by NewsAnalyst

        return {"fetch_results": fetch_results, "warnings": warnings}
```

**Reuse code from**: `app/graph/nodes/peer_filing_ingester.py` — the per-peer filing download loop. Extract into shared helper or copy the pattern.

**Timeout budget**: Add a configurable timeout (default 120s) from `app/core/config.py`. If budget exceeded, stop downloading remaining peers and add a warning.

---

## Phase 2: Specialist Node Modifications

### 2.1 BusinessAnalyst absorbs CultureAnalyst

**File**: `app/graph/nodes/business_analyst.py`

Two changes needed:
1. Add import: `from app.services.culture_governance_service import CultureGovernanceService`
2. Modify `BusinessAnalystNode.__call__()`:

```python
def __call__(self, state: ResearchState) -> dict:
    plan = state.get("research_plan", {})
    section_plan = plan.get("sections", {}).get("business_analysis", {})

    # Skip if plan says so
    if section_plan.get("skip"):
        return {"business_analysis": {}, "segment_analysis": {}, "culture_analysis": {}}

    focus_areas = section_plan.get("focus_areas", [])

    # Business analysis (existing)
    biz_result = self.service.analyze(
        session=self.session, ticker=state["ticker"],
        company_name=state["company_name"],
        company_profile=state.get("company_profile", {}),
        filing_coverage=state.get("filing_coverage", {}),
        model=self.model,
        focus_areas=focus_areas,  # NEW param — requires service change below
    )

    # Culture analysis (absorbed from CultureAnalyst)
    culture_plan = plan.get("sections", {}).get("culture_governance", {})
    culture_result = {}
    if not culture_plan.get("skip"):
        culture_service = CultureGovernanceService()
        culture_result = culture_service.analyze(
            session=self.session, ticker=state["ticker"],
            company_name=state["company_name"],
            company_profile=state.get("company_profile", {}),
            filing_coverage=state.get("filing_coverage", {}),
            model=self.model,
        )

    return {
        "business_analysis": biz_result.get("business_analysis", {}),
        "segment_analysis": biz_result.get("segment_analysis", {}),
        "culture_analysis": culture_result,
        "warnings": biz_result.get("warnings", []) + culture_result.get("warnings", []),
    }
```

**Service change required**: `BusinessAnalysisService.analyze()` (in `app/services/business_analysis_service.py`) does NOT currently have a `focus_areas` parameter. Its current signature is:

```python
def analyze(self, session, ticker, company_name, company_profile, filing_coverage, model=None)
```

Add `focus_areas: list[str] | None = None` as an optional parameter. When provided, use the focus_areas to scope evidence retrieval queries (e.g., pass to RAG query builder). When `None`, fall back to existing fixed query lists.

### 2.2 PeerAnalyzer → PeerAnalyst rename

**File**: `app/graph/nodes/peer_analyzer.py`

**Already done (partially)** — class is already renamed to `PeerAnalystNode` with backward-compat alias `PeerAnalyzerNode = PeerAnalystNode`. The skip check also already exists.

**Remaining change**: The current `PeerAnalystNode.__call__()` still calls `self.intelligence_service.discover_candidates()` to enrich peers and merges with `state.get("peer_list", [])`. In v3, it should use `state["research_plan"]["peer_candidates"]` as the authoritative peer list from ResearchPlanner, without additional discovery:

```python
def __call__(self, state: ResearchState) -> dict:
    plan = state.get("research_plan", {})
    section_plan = plan.get("sections", {}).get("peer_analysis", {})

    if section_plan.get("skip"):
        return {"peer_analysis": {}, "peer_comparison": {}}

    # v3: Use plan's peer list directly — ResearchPlanner already decided peers
    peer_candidates = plan.get("peer_candidates", [])
    # Remove the call to self.intelligence_service.discover_candidates()
    # ... rest of analysis logic using peer_candidates ...
```

**Decision needed**: Whether to completely remove the discovery enrichment step or keep it as a fallback when `research_plan` is empty. Recommend removing it — ResearchPlanner's deterministic fallback already calls `discover_top_peers_fast()`, so peers are always present in the plan.

### 2.3 NewsCollector → NewsAnalyst rename

**File**: `app/graph/nodes/news_collector.py`

**Already done (partially)** — class is already renamed to `NewsAnalystNode` with backward-compat alias `NewsCollectorNode = NewsAnalystNode`. Init takes `(session, news_service=None, event_service=None, model=None)`.

**Remaining change** — add cache freshness respect using `data_availability`:

```python
def __call__(self, state: ResearchState) -> dict:
    plan = state.get("research_plan", {})
    section_plan = plan.get("sections", {}).get("news_analysis", {})

    if section_plan.get("skip"):
        return {"company_news": [], "peer_news": [], "event_list": []}

    # Check news freshness from data_availability
    availability = state.get("data_availability", {})
    news_status = availability.get("news", {})
    if news_status.get("status") == "fresh":
        # Use cached news
        return self._load_cached_news(state)

    # ... existing fetch logic ...
```

### 2.4 FinancialAnalyst — financial_summary output

**File**: `app/graph/nodes/financial_analyst.py`

**Already done** — `FinancialAnalystNode.__call__()` already returns `financial_summary` in its output dict. No changes needed.

### 2.5 CompanyProfiler — force skip_ingestion=True

**File**: `app/graph/nodes/company_profiler.py`

**Already done (partially)** — `CompanyProfilerNode.__init__()` already accepts `skip_ingestion: bool = False` and its `__call__()` has plan-driven override logic: if `research_plan.sections.company_profile.skip` is True or if `_context.has_effective_filings` is True, it sets `skip = True`.

**Remaining change**: In v3, DataFetcher always runs before CompanyProfiler, so ingestion should always be skipped. Update workflow.py to instantiate with `skip_ingestion=True`:

```python
graph.add_node("company_profiler", CompanyProfilerNode(session, skip_ingestion=True))
```

Or rely on the existing plan-driven override. Either approach works — the explicit constructor param is safer.

---

## Phase 3: SynthesisReviewer (replaces InvestmentExpert + ReportCoverageChecker)

### 3.1 SynthesisReviewer node

**New file**: `app/graph/nodes/synthesis_reviewer.py`

```python
class SynthesisReviewerNode:
    def __init__(self, service: ReportService | None = None, 
                 coverage_service: ReportCoverageService | None = None,
                 model: str | None = None) -> None:
        self.service = service or ReportService()
        self.coverage_service = coverage_service or ReportCoverageService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        # Phase 1: deterministic quality gate
        coverage = self.coverage_service.check(state)
        warnings = coverage.get("warnings", [])

        # Phase 2: LLM synthesis
        try:
            report_md, report_json, final_rating = self.service.build(state, model=self.model)
        except Exception as exc:
            return {
                "final_report": "", "final_rating": "research_more",
                "report_json": {}, "report_coverage": coverage,
                "errors": [f"SynthesisReviewer failed: {exc}"],
            }

        # Inject research_questions into report_json
        plan = state.get("research_plan", {})
        report_json["research_questions"] = plan.get("research_questions", [])

        return {
            "final_report": report_md,
            "final_rating": final_rating,
            "report_json": report_json,
            "report_coverage": coverage,
            "warnings": warnings,
        }
```

This combines:
- `InvestmentExpertNode` → Phase 2 (LLM report build)
- `ReportCoverageCheckerNode` → Phase 1 (quality gate)

---

## Phase 4: Workflow DAG Rewiring

### 4.1 workflow.py rewrite

**File**: `app/graph/workflow.py`

```python
from app.graph.nodes.research_planner import ResearchPlannerNode          # NEW
from app.graph.nodes.data_availability_checker import DataAvailabilityCheckerNode  # NEW
from app.graph.nodes.data_fetcher import DataFetcherNode                  # NEW
from app.graph.nodes.company_profiler import CompanyProfilerNode
from app.graph.nodes.business_analyst import BusinessAnalystNode
from app.graph.nodes.financial_analyst import FinancialAnalystNode
from app.graph.nodes.news_collector import NewsAnalystNode    # class already renamed, file kept as news_collector.py
from app.graph.nodes.peer_analyzer import PeerAnalystNode     # class already renamed, file kept as peer_analyzer.py
from app.graph.nodes.valuation_agent import ValuationAgentNode
from app.graph.nodes.risk_analyst import RiskAnalystNode
from app.graph.nodes.synthesis_reviewer import SynthesisReviewerNode       # NEW

def build_research_workflow(session: Session):
    graph = StateGraph(ResearchState)

    # Stage 1-3: Sequential planning + data prep
    graph.add_node("research_planner", ResearchPlannerNode(session))
    graph.add_node("data_availability_checker", DataAvailabilityCheckerNode(session))
    graph.add_node("data_fetcher", DataFetcherNode(session))

    # Stage 4: Company profiling
    graph.add_node("company_profiler", CompanyProfilerNode(session))

    # Stage 5: Parallel specialists
    graph.add_node("business_analyst", BusinessAnalystNode(session))
    graph.add_node("financial_analyst", FinancialAnalystNode(session))
    graph.add_node("news_analyst", NewsAnalystNode(session))
    graph.add_node("peer_analyst", PeerAnalystNode(session=session))

    # Stage 6-8: Sequential synthesis
    graph.add_node("valuation_agent", ValuationAgentNode())
    graph.add_node("risk_analyst", RiskAnalystNode(session))
    graph.add_node("synthesis_reviewer", SynthesisReviewerNode())

    # Edges: sequential stages
    graph.add_edge(START, "research_planner")
    graph.add_edge("research_planner", "data_availability_checker")
    graph.add_edge("data_availability_checker", "data_fetcher")
    graph.add_edge("data_fetcher", "company_profiler")

    # Parallel fan-out from company_profiler
    graph.add_edge("company_profiler", "business_analyst")
    graph.add_edge("company_profiler", "financial_analyst")
    graph.add_edge("company_profiler", "news_analyst")
    graph.add_edge("company_profiler", "peer_analyst")

    # Parallel fan-in to valuation_agent
    graph.add_edge("business_analyst", "valuation_agent")
    graph.add_edge("financial_analyst", "valuation_agent")
    graph.add_edge("news_analyst", "valuation_agent")
    graph.add_edge("peer_analyst", "valuation_agent")

    # Sequential tail
    graph.add_edge("valuation_agent", "risk_analyst")
    graph.add_edge("risk_analyst", "synthesis_reviewer")
    graph.add_edge("synthesis_reviewer", END)

    return graph.compile()
```

### 4.2 Remove old imports

Remove from `workflow.py`:
- `ContextLoaderNode`
- `FilingCoverageCheckerNode`
- `CultureAnalystNode`
- `PeerFilingIngesterNode`
- `InvestmentExpertNode`
- `ReportCoverageCheckerNode`

### 4.3 File renames

Class renames are **already done** via backward-compat aliases:
- `news_collector.py` exports `NewsAnalystNode` (with alias `NewsCollectorNode`)
- `peer_analyzer.py` exports `PeerAnalystNode` (with alias `PeerAnalyzerNode`)

**Optional**: Rename the files themselves (`news_collector.py` → `news_analyst.py`, etc.) for consistency. Not blocking — imports work either way.

Files no longer imported by v3 workflow (keep for reference, do not delete):
- `nodes/context_loader.py` — logic migrated to DataAvailabilityChecker
- `nodes/filing_coverage_checker.py` — logic migrated to DataAvailabilityChecker
- `nodes/peer_filing_ingester.py` — logic migrated to DataFetcher
- `nodes/culture_analyst.py` — logic absorbed into BusinessAnalyst
- `nodes/investment_expert.py` — logic migrated to SynthesisReviewer
- `nodes/report_coverage_checker.py` — logic migrated to SynthesisReviewer

---

## Phase 5: ResearchRunner Update

### 5.1 WORKFLOW_STEPS update

**File**: `app/services/research_runner.py`

Replace `WORKFLOW_STEPS`:

```python
WORKFLOW_STEPS: list[tuple[str, str, type]] = [
    ("research_planner", "ResearchPlanner", ResearchPlannerNode),
    ("data_availability_checker", "DataAvailabilityChecker", DataAvailabilityCheckerNode),
    ("data_fetcher", "DataFetcher", DataFetcherNode),
    ("company_profiler", "CompanyProfiler", CompanyProfilerNode),
    ("business_analyst", "BusinessAnalyst", BusinessAnalystNode),
    ("peer_analyst", "PeerAnalyst", PeerAnalystNode),
    ("news_analyst", "NewsAnalyst", NewsAnalystNode),
    ("financial_analyst", "FinancialAnalyst", FinancialAnalystNode),
    ("valuation_agent", "ValuationAgent", ValuationAgentNode),
    ("risk_analyst", "RiskAnalyst", RiskAnalystNode),
    ("synthesis_reviewer", "SynthesisReviewer", SynthesisReviewerNode),
]
```

Note: `business_analyst`, `peer_analyst`, `news_analyst`, `financial_analyst` run in parallel in the LangGraph DAG, but ResearchRunner runs them sequentially in its fallback mode. This is fine — the sequential runner is a backup.

### 5.2 _build_node update

```python
def _build_node(self, step_id: str, node_cls: type) -> Any:
    if step_id == "research_planner":
        return node_cls(self.session, model=self.model)
    if step_id in {"data_availability_checker", "data_fetcher"}:
        return node_cls(self.session)
    if step_id == "company_profiler":
        return node_cls(self.session, model=self.model)
    if step_id in {"business_analyst", "news_analyst"}:
        return node_cls(self.session, model=self.model)
    if step_id == "peer_analyst":
        return node_cls(session=self.session, model=self.model)
    if step_id in {"financial_analyst", "risk_analyst"}:
        return node_cls(self.session)
    if step_id == "synthesis_reviewer":
        return node_cls(model=self.model)
    return node_cls()
```

### 5.3 _build_step_details update

Add details for new nodes:

```python
if step_id == "research_planner":
    plan = state.get("research_plan", {})
    return {
        "peer_count": len(plan.get("peer_candidates", [])),
        "peers": [p.get("ticker") for p in plan.get("peer_candidates", [])],
        "question_count": len(plan.get("research_questions", [])),
        "skipped_sections": [s for s, c in plan.get("sections", {}).items() if c.get("skip")],
    }
if step_id == "data_availability_checker":
    avail = state.get("data_availability", {})
    fn = avail.get("fetch_needed", {})
    return {
        "target_filing_available": avail.get("target", {}).get("filings", {}).get("status") == "available",
        "fetch_needed_count": sum(1 for v in fn.values() if v),
    }
if step_id == "data_fetcher":
    fr = state.get("fetch_results", {})
    return {
        "target_fetched": fr.get("target_filings", False),
        "peer_filings_fetched": len([v for v in fr.get("peer_filings", {}).values() if v]),
    }
if step_id == "synthesis_reviewer":
    return {
        "final_rating": state.get("final_rating"),
        "coverage_gaps": state.get("report_coverage", {}).get("gaps", []),
    }
```

### 5.4 _commit_checkpoint update

Add new node IDs to the checkpoint set:

```python
if step_id in {"data_fetcher", "news_analyst", "peer_analyst", ...}:
    self.session.commit()
```

### 5.5 Remove old imports

Remove imports for: `ContextLoaderNode`, `CultureAnalystNode`, `FilingCoverageCheckerNode`, `PeerFilingIngesterNode`, `InvestmentExpertNode`, `ReportCoverageCheckerNode`.

---

## Phase 6: API Endpoint

### 6.1 New endpoint: GET /research/{ticker}/plan

**File**: `app/api/routes/research.py`

```python
@router.get("/{ticker}/plan")
def get_research_plan(ticker: str, session: Session = Depends(get_db_session)):
    """Return the most recent research plan for a ticker."""
    ticker = normalize_ticker(ticker)
    report = get_latest_report_by_ticker(session, ticker)
    if not report or not report.report_json:
        raise HTTPException(status_code=404, detail="No research plan found")
    plan = report.report_json.get("research_plan") or report.report_json.get("_research_plan")
    if not plan:
        raise HTTPException(status_code=404, detail="Report has no research plan")
    return plan
```

To support this, `ResearchRunner._persist_outputs()` should save `research_plan` into `report_json["_research_plan"]`.

### 6.2 Prewarm endpoint update

Add optional `peers: list[str]` parameter to `PrewarmRequest` schema:

**File**: `app/schemas/research.py`

Current schema (only has `ticker` and `company_name`):
```python
class PrewarmRequest(BaseModel):
    ticker: str
    company_name: str
```

Add `peers` field:
```python
class PrewarmRequest(BaseModel):
    ticker: str
    company_name: str
    peers: list[str] = []  # NEW: optional peer tickers to prewarm
```

---

## Phase 7: Tests

### 7.1 New test file: `tests/test_v3_workflow.py`

Test cases to write:

1. **test_research_planner_llm_output** — mock LLM, verify research_plan JSON structure
2. **test_research_planner_deterministic_fallback** — simulate LLM failure, verify fallback plan
3. **test_data_availability_checker_all_available** — mock DB with all data present, verify fetch_needed is empty
4. **test_data_availability_checker_missing_peer** — one peer has no filings, verify it appears in fetch_needed
5. **test_data_fetcher_skips_available** — verify DataFetcher only downloads what fetch_needed says
6. **test_data_fetcher_timeout_budget** — verify timeout stops further downloads
7. **test_business_analyst_skip** — verify skip=true returns empty dicts
8. **test_business_analyst_includes_culture** — verify culture_analysis in output
9. **test_peer_analyst_uses_plan_peers** — verify PeerAnalyst uses plan's peer_candidates, not discover_top_peers
10. **test_news_analyst_skip_fresh_cache** — verify cached news is reused when fresh
11. **test_synthesis_reviewer_quality_gate** — verify Phase 1 detects missing sections
12. **test_synthesis_reviewer_injects_questions** — verify research_questions in report_json
13. **test_v3_workflow_graph_structure** — verify node count (10), edges, parallel fan-out/fan-in
14. **test_research_runner_v3_steps** — verify WORKFLOW_STEPS has correct entries

### 7.2 Update existing tests

**File**: `tests/test_optimization_refactor.py`

- Update `test_workflow_graph_has_context_loader` → `test_workflow_graph_has_research_planner`
- Update node ID assertions to match new names
- Update step count from 13 to 10 (if testing WORKFLOW_STEPS length)

---

## Execution Order

Execute in this order to minimize breakage. Items marked ✅ are already done.

| Step | What | Files | Risk |
|---|---|---|---|
| ✅ | State fields already added | `state.py` | Done |
| ✅ | Class renames already done | `peer_analyzer.py`, `news_collector.py` | Done (aliases) |
| ✅ | financial_summary output already exists | `financial_analyst.py` | Done |
| ✅ | skip_ingestion param already exists | `company_profiler.py` | Done |
| 1 | Create ResearchPlannerNode | `nodes/research_planner.py` (new) | Low — new file |
| 2 | Create DataAvailabilityCheckerNode | `nodes/data_availability_checker.py` (new) | Low — new file |
| 3 | Create DataFetcherNode | `nodes/data_fetcher.py` (new) | Low — new file |
| 4 | Create SynthesisReviewerNode | `nodes/synthesis_reviewer.py` (new) | Low — new file |
| 5 | Modify BusinessAnalyst (absorb culture) | `nodes/business_analyst.py` + `business_analysis_service.py` | Medium |
| 6 | Modify PeerAnalyst (use plan peers) | `nodes/peer_analyzer.py` | Medium — behavioral change |
| 7 | Add news cache freshness check | `nodes/news_collector.py` | Low |
| 8 | Rewrite workflow.py | `workflow.py` | **High** — changes DAG |
| 9 | Update ResearchRunner | `research_runner.py` | **High** — changes execution |
| 10 | Add API endpoint | `routes/research.py`, `schemas/research.py` | Low |
| 11 | Write tests | `tests/test_v3_workflow.py` | None |
| 12 | Run full test suite | — | Validation |

Steps 1-4 can be done in parallel (all new files). Steps 8-9 must be done together (DAG + runner must match).

---

## Key Code Patterns

### Pattern: Specialist skip check
Every specialist node should start with:
```python
plan = state.get("research_plan", {})
section_plan = plan.get("sections", {}).get("<section_name>", {})
if section_plan.get("skip"):
    return {<empty outputs>}
focus_areas = section_plan.get("focus_areas", [])
depth = section_plan.get("depth", "standard")
```

### Pattern: ResearchPlanner JSON parsing
```python
from app.services.aliyun_llm_service import get_llm_service

llm = get_llm_service()

# Option A: use chat_json() which returns dict | None directly
plan = llm.chat_json(messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": f"Ticker: {ticker}, Company: {company_name}"}],
                      model=self.model)
if plan is None or "sections" not in plan or "peer_candidates" not in plan:
    logger.warning("research_planner_llm_parse_failed")
    plan = None  # triggers deterministic fallback

# Option B: use chat() which returns str | None, then parse manually
# response = llm.chat(messages=[...], model=self.model)
# if response:
#     plan = json.loads(response)  # response is str, not object with .content
```

### Pattern: DataAvailabilityChecker filing check
```python
from app.db.repositories.filing_repository import get_filings, is_effective_filing, get_filing_chunks_by_filing

def _check_target(self, company):
    filings = get_filings(self.session, company.id)
    effective = [f for f in filings if is_effective_filing(f)]

    # No get_filing_chunks_count() exists — count by iterating filings
    chunk_count = sum(len(get_filing_chunks_by_filing(self.session, f.id)) for f in effective)

    return {
        "filings": {
            "status": "available" if effective else "missing",
            "effective_count": len(effective),
        },
        "filing_chunks": {
            "status": "available" if chunk_count > 0 else "missing",
            "count": chunk_count,
        },
        # ... financial_data, company_profile, basic_info
    }
```

**Note**: If chunk counting becomes a performance issue, add a `get_filing_chunks_count(session, company_id) -> int` helper to `filing_repository.py` using `SELECT COUNT(*)` instead of fetching all rows.

---

## Files Summary

### New files (5)
- `app/graph/nodes/research_planner.py`
- `app/graph/nodes/data_availability_checker.py`
- `app/graph/nodes/data_fetcher.py`
- `app/graph/nodes/synthesis_reviewer.py`
- `tests/test_v3_workflow.py`

### Modified files (6)
- `app/graph/workflow.py` — full rewrite (DAG rewiring)
- `app/graph/nodes/business_analyst.py` — absorb culture, add focus_areas
- `app/graph/nodes/peer_analyzer.py` — use plan's peer_candidates instead of discovery
- `app/graph/nodes/news_collector.py` — add news cache freshness check
- `app/services/business_analysis_service.py` — add `focus_areas` param to `analyze()`
- `app/services/research_runner.py` — update WORKFLOW_STEPS + _build_node + _build_step_details
- `app/api/routes/research.py` — add GET /research/{ticker}/plan
- `app/schemas/research.py` — add `peers` field to PrewarmRequest

### No changes needed (already done)
- `app/graph/state.py` — fields already exist
- `app/graph/nodes/financial_analyst.py` — financial_summary already in output
- `app/graph/nodes/company_profiler.py` — skip_ingestion already supported

### Deprecated files (6, do not delete yet)
- `app/graph/nodes/context_loader.py`
- `app/graph/nodes/filing_coverage_checker.py`
- `app/graph/nodes/culture_analyst.py`
- `app/graph/nodes/peer_filing_ingester.py`
- `app/graph/nodes/investment_expert.py`
- `app/graph/nodes/report_coverage_checker.py`

---

## Validation Checklist

After implementation, verify:

- [ ] `pytest tests/test_v3_workflow.py` — all new tests pass
- [ ] `pytest tests/` — full suite passes (no regressions)
- [ ] `POST /research/run` with a known ticker produces a complete report
- [ ] Report JSON contains `research_questions` from the plan
- [ ] `GET /research/{ticker}/plan` returns the plan
- [ ] Peer annual reports are only downloaded when missing
- [ ] If LLM is down, deterministic fallback produces a valid plan
- [ ] BusinessAnalyst output includes `culture_analysis`
- [ ] Specialist skip works (set a section to skip=true in plan, verify empty output)
