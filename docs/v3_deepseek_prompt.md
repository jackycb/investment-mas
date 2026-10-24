# Task: Implement V3 Expert-Led Workflow Refactor

You are working on the `investment-mas` project — a LangGraph-based investment research multi-agent system.

Your job: refactor the research workflow from v2 (Staged Parallel, 13 nodes) to v3 (Expert-Led Planning + Targeted Execution, 10 nodes).

## Required Reading

Before writing any code, read these files thoroughly:

1. `docs/v3_implementation_plan.md` — the step-by-step implementation plan with code patterns, file lists, and execution order
2. `docs/investment_mas_system_architecture.md` — sections 5.1 (v3 workflow design), 6 (node definitions), 14 (target DAG)
3. `app/graph/workflow.py` — current v2 DAG you're replacing
4. `app/graph/state.py` — current state definition (already has v3 fields)
5. `app/services/research_runner.py` — sequential runner that must match the new DAG

## Key Facts About Current Codebase

Things that are ALREADY DONE (do not redo):
- `ResearchState` already has `research_plan: dict`, `data_availability: dict`, `fetch_results: dict`
- `PeerAnalystNode` class already exists in `peer_analyzer.py` (with backward-compat alias `PeerAnalyzerNode`)
- `NewsAnalystNode` class already exists in `news_collector.py` (with backward-compat alias `NewsCollectorNode`)
- `FinancialAnalystNode` already outputs `financial_summary`
- `CompanyProfilerNode` already supports `skip_ingestion` param and plan-driven override
- All specialist nodes already have skip-check logic reading `research_plan.sections`

Things that NEED DOING:
- 4 new node files: `research_planner.py`, `data_availability_checker.py`, `data_fetcher.py`, `synthesis_reviewer.py`
- `BusinessAnalystNode` must absorb `CultureAnalyst` logic (call `CultureGovernanceService` internally)
- `BusinessAnalysisService.analyze()` needs a new `focus_areas: list[str] | None = None` param
- `PeerAnalystNode` must stop calling `discover_candidates()` and use `research_plan.peer_candidates` instead
- `workflow.py` must be rewritten to the 10-node v3 DAG
- `research_runner.py` WORKFLOW_STEPS, `_build_node`, `_build_step_details` must be updated
- New API endpoint `GET /research/{ticker}/plan`
- `PrewarmRequest` needs a `peers: list[str] = []` field

## Execution Order

Work in this exact order. Run `pytest tests/` after each phase.

### Phase A: Create 4 new node files (can be done in parallel)

1. **`app/graph/nodes/research_planner.py`** — `ResearchPlannerNode(session, model=None)`
   - LLM planning via `get_llm_service().chat_json()` (returns `dict | None`, NOT an object with `.content`)
   - Deterministic fallback using `PeerIntelligenceService().discover_top_peers_fast()`
   - Output: `{"research_plan": {...}}`
   - See plan section 1.2 for full spec

2. **`app/graph/nodes/data_availability_checker.py`** — `DataAvailabilityCheckerNode(session)`
   - No LLM. DB/filesystem checks only.
   - Reuse logic from `ContextLoaderNode` (company lookup, filing check) and `FilingCoverageService.check()`
   - Use `SectionCacheService.load_cached_state()` for cache check
   - No `get_filing_chunks_count()` function exists — use `sum(len(get_filing_chunks_by_filing(session, f.id)) for f in effective)`
   - Output: `{"data_availability": {...}, "filing_coverage": {...}, "_context": {...}}`
   - See plan section 1.3 for full spec

3. **`app/graph/nodes/data_fetcher.py`** — `DataFetcherNode(session, filing_service=None)`
   - No LLM. Only downloads what `fetch_needed` specifies.
   - Reuse per-peer download logic from `PeerFilingIngesterNode.__call__()`
   - `FilingService.ingest_company_filings(session, company, market, years=1, filing_types={"annual_report"})` returns `tuple[list[Filing], list[str]]`
   - Output: `{"fetch_results": {...}, "warnings": [...]}`
   - See plan section 1.4 for full spec

4. **`app/graph/nodes/synthesis_reviewer.py`** — `SynthesisReviewerNode(service=None, coverage_service=None, model=None)`
   - Combines `InvestmentExpertNode` + `ReportCoverageCheckerNode`
   - Phase 1: `ReportCoverageService().check(state)` — deterministic quality gate
   - Phase 2: `ReportService().build(state, model=self.model)` — returns `tuple[str, dict, str]` (markdown, json, rating)
   - Inject `research_plan.research_questions` into `report_json`
   - Output: `{"final_report": ..., "final_rating": ..., "report_json": ..., "report_coverage": ..., "warnings": [...]}`
   - See plan section 3.1 for full spec

### Phase B: Modify existing nodes

5. **`app/graph/nodes/business_analyst.py`** — absorb CultureAnalyst
   - Add `from app.services.culture_governance_service import CultureGovernanceService`
   - In `__call__`: after business analysis, call `CultureGovernanceService().analyze(session, ticker, company_name, company_profile, filing_coverage, model)` unless `culture_governance` section is skipped
   - Return `culture_analysis` in output dict
   - See plan section 2.1

6. **`app/services/business_analysis_service.py`** — add `focus_areas` param
   - Add `focus_areas: list[str] | None = None` to `analyze()` method signature
   - When provided, use to scope evidence retrieval queries; when `None`, use existing fixed query lists

7. **`app/graph/nodes/peer_analyzer.py`** — use plan peers
   - In `__call__`: get peers from `state.get("research_plan", {}).get("peer_candidates", [])` instead of calling `self.intelligence_service.discover_candidates()`
   - Keep the rest of the analysis logic intact

### Phase C: Rewire DAG + Runner (do together)

8. **`app/graph/workflow.py`** — full rewrite
   - Import from existing filenames: `from app.graph.nodes.news_collector import NewsAnalystNode`, `from app.graph.nodes.peer_analyzer import PeerAnalystNode`
   - Import new nodes: `ResearchPlannerNode`, `DataAvailabilityCheckerNode`, `DataFetcherNode`, `SynthesisReviewerNode`
   - Remove old imports: `ContextLoaderNode`, `FilingCoverageCheckerNode`, `CultureAnalystNode`, `PeerFilingIngesterNode`, `InvestmentExpertNode`, `ReportCoverageCheckerNode`
   - Build 10-node DAG:
     ```
     START → research_planner → data_availability_checker → data_fetcher → company_profiler
       → [business_analyst | financial_analyst | news_analyst | peer_analyst]
       → valuation_agent → risk_analyst → synthesis_reviewer → END
     ```
   - Instantiate `CompanyProfilerNode(session, skip_ingestion=True)`

9. **`app/services/research_runner.py`** — update to match new DAG
   - Replace WORKFLOW_STEPS (11 entries, down from 13)
   - Update `_build_node()` dispatch
   - Update `_build_step_details()` for new node IDs
   - Update `_commit_checkpoint()` set
   - Remove old imports, add new imports
   - In `_persist_outputs()`: save `state.get("research_plan")` into `report_json["_research_plan"]`

### Phase D: API + Schema

10. **`app/api/routes/research.py`** — add `GET /research/{ticker}/plan` endpoint
11. **`app/schemas/research.py`** — add `peers: list[str] = []` to `PrewarmRequest`

### Phase E: Tests

12. **Create `tests/test_v3_workflow.py`** with test cases listed in plan section 7.1
13. **Update `tests/test_optimization_refactor.py`** — fix node name assertions
14. **Run full suite**: `pytest tests/` — all must pass

## Critical Gotchas

- `AliyunLLMService.chat()` returns `str | None` (NOT an object). Use `chat_json()` for direct dict output.
- `FilingService.ingest_company_filings()` returns `tuple[list[Filing], list[str]]` — filings and warnings.
- There is NO `get_filing_chunks_count()` in `filing_repository.py`. Use `get_filing_chunks_by_filing()` and count.
- `CultureGovernanceService.analyze()` signature: `(session, ticker, company_name, company_profile, filing_coverage, model=None)`.
- `ReportService.build()` returns 3-tuple: `(markdown_str, report_json_dict, final_rating_str)`.
- `ReportCoverageService.check()` takes a single `state: dict` param.
- Use `.venv/bin/python` for running tests and server (miniconda3 lacks chromadb).
- Parallel branches in LangGraph must use `Annotated[list, _merge_lists]` reducer for shared list fields. `errors` and `warnings` already use this.
- Do NOT write to `_context` dict from parallel branches — it will cause merge conflicts.

## Definition of Done

- [ ] All 4 new node files created and working
- [ ] BusinessAnalyst absorbs culture analysis
- [ ] PeerAnalyst uses plan's peer_candidates
- [ ] workflow.py has 10-node v3 DAG
- [ ] ResearchRunner matches the new DAG
- [ ] `GET /research/{ticker}/plan` returns research plan
- [ ] `pytest tests/` passes with 0 failures
- [ ] `POST /research/run` with ticker `HK.00700` or `US.NVDA` produces a complete report
