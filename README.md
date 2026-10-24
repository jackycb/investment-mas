# Investment MAS

An investment research Multi-Agent System built on Python 3.11, LangGraph, FastAPI, PostgreSQL, SQLAlchemy, and Alembic.

- Financial data reads from local directory first
- Public information fetched via web scraping
- Uses Alibaba Cloud DashScope / Qwen / DeepSeek for structured analysis, news event enrichment, and comprehensive research report generation
- All LLM prompts, reports, and UI are in English

## Project Structure

```text
investment_mas/
  app/
    api/
      routes/
        health.py
        research.py            # /run, /stream, /chat, /followup, /feedback, /filings, /documents,
                               # /prewarm, /refresh-peers, /rerun-section, /{ticker}/plan
    core/
      config.py
      database.py
      logging.py
    db/
      base.py
      models/
        company.py             # Company, CompanyProfile, PeerMapping
        document.py            # CompanyDocument, DocumentChunk
        evidence.py            # EvidenceSpan
        filing.py              # Filing, FilingChunk
        financial.py           # FinancialReport, FinancialPeerSnapshot
        management.py          # ManagementProfile, IncentiveRecord
        news.py                # NewsArticle, NewsEvent
        research.py            # ResearchReport, RiskAssessment, ValuationResult
        segment.py             # CompanySegmentProfile
        session.py             # ReportSession, FollowupQuestion, UserFeedback
      repositories/
    graph/
      state.py                 # ResearchState with Annotated reducers for parallel branches
      workflow.py              # V3 Expert-Led LangGraph workflow (11 nodes, 4 parallel branches)
      nodes/
        research_planner.py    # Stage 1: LLM research plan (scope, peers, data needs)
        data_availability_checker.py  # Stage 2: deterministic data readiness check
        data_fetcher.py        # Stage 3: targeted fetch of missing data only
        company_profiler.py    # Stage 4: plan-aware profile (skip_ingestion=True)
        business_analyst.py    # Stage 5A: business + culture/governance analysis
        news_collector.py      # Stage 5B: news + event extraction (exports NewsAnalystNode)
        financial_analyst.py   # Stage 5C: financial analysis + red flags
        peer_analyzer.py       # Stage 5D: peer discovery + metrics (exports PeerAnalystNode)
        valuation_agent.py     # Stage 6: valuation
        risk_analyst.py        # Stage 6: risk assessment
        synthesis_reviewer.py  # Stage 7: quality gate + LLM report synthesis
        # Legacy nodes (kept for backward compatibility, not used in v3 workflow):
        context_loader.py
        culture_analyst.py
        filing_coverage_checker.py
        peer_filing_ingester.py
        investment_expert.py
        report_coverage_checker.py
    providers/
      company_info_provider.py
      embedding_provider.py    # LocalHashEmbeddingProvider
      filing_discovery_provider.py   # SEC EDGAR / HKEXnews / CNINFO adapters
      filing_download_provider.py
      financial_data_provider.py
      news_provider.py
      vector_store_provider.py # ChromaVectorStoreProvider / NullVectorStoreProvider
      stock_price_provider.py
    schemas/
    services/
      research_runner.py               # Sequential runner with SSE streaming + checkpoints
      aliyun_llm_service.py            # DashScope / DeepSeek LLM client
      business_analysis_service.py     # Market space, segments, competition deep-dive
      company_profile_rag_service.py   # Filing RAG with bilingual queries + keyword fallback
      company_profile_service.py       # Profile orchestration
      culture_governance_service.py    # Management, compensation, governance
      feedback_refinement_service.py   # Section-level report refinement
      filing_service.py                # End-to-end filing pipeline (discover → download → parse → chunk → embed → store)
      financial_service.py             # Financial data loading + metric computation
      followup_qa_service.py           # Evidence-grounded follow-up QA
      news_service.py                  # News collection + dedup
      peer_analysis_service.py         # Peer classification
      peer_intelligence_service.py     # Multi-source peer candidate generation + metric snapshots
      report_service.py                # LLM report generation
      report_coverage_service.py       # Post-report quality gate
      retrieval_service.py             # Vector + keyword filing chunk search
      section_cache_service.py         # Section-level caching in report_json (7-day expiry)
      valuation_service.py             # Valuation computation
    utils/
      file_discovery.py
      financial_mapping.py
      financial_parser.py
      metadata_utils.py
    web/
      index.html               # English web UI
    main.py
  alembic/
    versions/
  docs/
    investment_mas_system_architecture.md  # Detailed architecture spec (Chinese)
    v3_implementation_plan.md              # V3 refactor plan
  scripts/
    seed_demo_data.py
    remediate_xbrl_chunks.py
    check_runtime.py            # Verify active Python runtime dependencies
    run_api.sh                  # Start API with project .venv, avoiding conda/base mismatch
  tests/
    test_v3_workflow.py                    # V3 workflow integration tests
    test_optimization_refactor.py          # Peer intelligence, caching, staging tests
    test_nvidia_retrieval_regression.py    # 8 regression tests for US filing retrieval
    test_research_runner.py                # Research runner tests
    ...
  Dockerfile
  docker-compose.yml
  .env.example
  deploy/
    ecs/
      .env.ecs.example
      docker-compose.ecs.yml
      deploy_to_ecs.sh
  pyproject.toml
  README.md
```

## Features

- **V3 Expert-Led LangGraph workflow** (11 nodes, 4 concurrent branches):
  ```
  ResearchPlanner → DataAvailabilityChecker → DataFetcher → CompanyProfiler
      ├── BusinessAnalyst ──────────────────┐
      ├── NewsAnalyst ──────────────────────┼── ValuationAgent → RiskAnalyst
      ├── FinancialAnalyst ────────────────┤
      └── PeerAnalyst ─────────────────────┘
  RiskAnalyst → SynthesisReviewer → END
  ```
- **ResearchPlanner**: LLM-driven planning — determines research scope, selects top peers, identifies data requirements, and formulates research questions before any data is fetched
- **DataAvailabilityChecker**: deterministic pre-flight inventory of local data (filings, financials, profiles, news, section cache), outputs `fetch_needed` so only missing data is retrieved
- **DataFetcher**: targeted data acquisition — only fetches what `fetch_needed` specifies; uses `NullVectorStoreProvider` for peer filings to skip ChromaDB and store chunks in PostgreSQL only
- **CompanyProfiler**: plan-aware profile generation with `skip_ingestion=True`; uses filing evidence retrieval (vector store or keyword-based fallback from PostgreSQL chunks)
- **Parallel specialists** (plan-aware: each reads `research_plan.sections` for skip/focus_areas/depth):
  - **BusinessAnalyst**: business model + segments + culture/governance (absorbs former CultureAnalyst)
  - **NewsAnalyst**: news collection + event extraction (respects cache freshness)
  - **FinancialAnalyst**: structured financial data + LLM synthesis + red flags
  - **PeerAnalyst**: uses ResearchPlanner's peer list; fetches peer metrics from Yahoo Finance (TTM) for consistent cross-company comparison
- **SynthesisReviewer**: two-phase quality gate (deterministic coverage check) + LLM report synthesis (replaces former InvestmentExpert + ReportCoverageChecker)
- **Section-level caching**: cacheable sections stored in `report_json._section_cache` for next-run reuse (7-day expiry); `DataAvailabilityChecker` restores cached sections into state
- **Peer financial comparison via Yahoo Finance**: peer metrics (revenue, margins, ROE) fetched from Yahoo Finance TTM data for all peers and the target company, ensuring consistent single-source comparison; revenue displayed in millions with currency unit (USD/CNY/HKD); target company shown first in bold; peer count capped at 3 direct industry peers
- Filing knowledge retrieval augmentation (RAG): auto-discover, download, parse SEC/HKEXnews/CNINFO annual reports, chunk and build local vector index for profiling/analysis/risk nodes
- Bilingual query templates: supports both English and Chinese filings (US 10-K, HK annual reports, A-share annual reports)
- Financial data from local directory + database fallback
- News/announcements/public info via web scraping
- Report generation via Alibaba Cloud DashScope / Qwen / DeepSeek
- Interactive research: `/research/followup` (evidence-grounded Q&A), `/research/feedback` (section-level report refinement)
- **API endpoints**: `/research/prewarm` (background filing ingestion), `/research/refresh-peers` (re-discover peers), `/research/rerun-section` (refine a single section), `GET /research/{ticker}/plan` (view research plan), `GET /research/{ticker}/report.docx` (Word export)
- Database persistence:
  - `companies`, `company_profiles`, `company_segment_profiles`, `peer_mappings`
  - `financial_reports`, `financial_peer_snapshots`
  - `news_articles`, `news_events`
  - `risk_assessments`, `valuation_results`, `research_reports`
  - `filings` / `filing_chunks`
  - `management_profiles` / `incentive_records`
  - `evidence_spans`
  - `report_sessions` / `followup_questions` / `user_feedback`
- Outputs Markdown report with peer metric comparison table (company names, revenue in millions with currency, target first), structured JSON, and Word (.docx) export

## Installation

```bash
cd /Users/jacky/Ai-code/investment-mas
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Start PostgreSQL

Using Docker Compose:

```bash
docker compose up -d db
```

Default connection string:

```bash
export INVESTMENT_MAS_DATABASE_URL="postgresql+psycopg://jacky@localhost:5432/investment_mas"
```

Note: When PostgreSQL is installed via Homebrew on macOS, the default role is typically your macOS username (e.g., `jacky`), not `postgres`. Docker containers can explicitly set `postgresql+psycopg://postgres:postgres@db:5432/investment_mas`.

## Configure Local Financial Data Directory

Default local financial data directory:

```bash
/Users/jacky/Ai-code/investment-mas/data/finance_report_web
```

Override via environment variables:

```bash
export INVESTMENT_MAS_LOCAL_FINANCIAL_BASE_DIR="/your/finance_report_web"
export INVESTMENT_MAS_FINANCIAL_DATA_PROVIDER="hybrid"
export INVESTMENT_MAS_ENABLE_FUTU_DOWNLOAD_FALLBACK="true"
export INVESTMENT_MAS_ENABLE_LEGACY_FINANCIAL_METRIC_ENRICHMENT="true"
export INVESTMENT_MAS_LEGACY_FINANCIAL_PLOT_ENABLED="false"
```

Provider modes:

- `hybrid`: Default, reads local directory first, falls back to database
- `local`: Local directory only
- `database`: PostgreSQL only

LocalFinancialDataProvider recursively scans the directory for `csv`, `xlsx`, `json` files and supports common English and Chinese field aliases (e.g., `revenue / total_revenue`, `net_income`, `gross_margin`, `roe`).

If the directory is missing, empty, or the target company file is absent:

1. System logs a warning
2. If `INVESTMENT_MAS_ENABLE_FUTU_DOWNLOAD_FALLBACK=true`, attempts `scripts/download_financial_data.py`
3. In `hybrid` mode, falls back to database provider
4. If database also has no data, returns partial research results with incomplete analysis noted in errors/report

## Financial Metric Enrichment

Legacy analysis scripts are consolidated into the project:

- Source: `scripts/stock_analysis_futu.py`
- Download script: `scripts/download_financial_data.py`

`FinancialAnalyst` auto-computes missing `gross_margin`, `net_margin`, `roe`, `roic` metrics.

```bash
export INVESTMENT_MAS_ENABLE_LEGACY_FINANCIAL_METRIC_ENRICHMENT=true
export INVESTMENT_MAS_LEGACY_FINANCIAL_PLOT_ENABLED=false
export INVESTMENT_MAS_LEGACY_FINANCIAL_PARAMETER_DIR="/Users/jacky/Ai-code/investment-mas/data/parameters"
```

## Configure Data Sources and LLM

Recommended configuration:

```bash
export INVESTMENT_MAS_NEWS_PROVIDER="web"
export INVESTMENT_MAS_COMPANY_INFO_PROVIDER="web"
export INVESTMENT_MAS_DASHSCOPE_API_KEY="your-dashscope-api-key"
export INVESTMENT_MAS_DASHSCOPE_BASE_URL="https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"
export INVESTMENT_MAS_DASHSCOPE_MODEL="auto"  # Available: auto, qwen3.8-max, qwen3.8-flash, qwen3.7-plus, deepseek-v4.1-flash, deepseek-v4-pro, glm-5.3
export INVESTMENT_MAS_DASHSCOPE_TEMPERATURE="0.2"
export INVESTMENT_MAS_ENABLE_LLM_REPORT="true"
```

Uses DashScope OpenAI-compatible Chat API (`/chat/completions`). Can also reuse existing DashScope config files:

```bash
export INVESTMENT_MAS_DASHSCOPE_CONFIG_PATH="/path/to/llm_config.json"
```

Two provider caveats worth knowing:

- The `auto` router rejects a `temperature` argument (`HTTP 400 — "auto does not support
  parameter(s): temperature"`), so `AliyunLLMService.chat()` omits it for `auto` and keeps it
  for every named model.
- All catalogued models — including `deepseek-v4-pro` and `deepseek-v4.1-flash` — stay on the
  gateway above, because token-plan serves them. `deepseek-*` only routes to the native
  `api.deepseek.com` when `INVESTMENT_MAS_ENABLE_DEEPSEEK_DIRECT_ROUTING=true`.

Configuration priority: JSON config file → `.env` variables → runtime parameters.

Data source strategy:

- Financial data: local `data/finance_report_web` → Futu download → PostgreSQL fallback
- Peer comparison metrics: Yahoo Finance TTM (yfinance) — single consistent source for company + all peers
- Company profile: curated fallback → Filing RAG → LLM synthesis
- Peers: ResearchPlanner LLM recommendation (1-3 direct industry peers) → static mapping → industry tag matching
- News: Google News RSS → SEC EDGAR submissions API
- US filings: SEC EDGAR official adapter
- HK filings: HKEXnews official adapter
- CN filings: CNINFO official adapter
- Report generation: DashScope Chat API
- Supplemental valuation: `scripts/run_value_analysis_by_code.py`

If network requests fail or API key is not configured, the system gracefully degrades: profiles fall back to database/static mapping, news falls back to rule-based extraction, reports fall back to deterministic templates.

## Run Migrations

```bash
alembic upgrade head
```

## Filing Knowledge Layer

The filing knowledge pipeline:

1. `FilingDiscoveryProvider` discovers annual report / periodic report candidates
2. `FilingDownloadProvider` downloads raw files
3. `DocumentStorageService` saves to local directory with sidecar metadata
4. `DocumentParserService` parses PDF / HTML / TXT
5. `ChunkingService` splits into chunks
6. `EmbeddingService` generates vectors
7. `VectorStoreProvider` persists chunks to local vector store (or `NullVectorStoreProvider` skips this step)
8. `RetrievalService` serves queries for `CompanyProfiler` / `RiskAnalyst` (vector search with keyword-based PostgreSQL fallback)

### Local Directories

```bash
data/company_docs/{market}/{ticker}/{filing_type}/{fiscal_year}/
data/vector_store/
```

### Environment Variables

```bash
export INVESTMENT_MAS_DOCUMENT_STORAGE_DIR="/Users/jacky/Ai-code/investment-mas/data/company_docs"
export INVESTMENT_MAS_VECTOR_STORE_TYPE="chroma"   # or "none" to skip ChromaDB entirely
export INVESTMENT_MAS_VECTOR_STORE_DIR="/Users/jacky/Ai-code/investment-mas/data/vector_store"
export INVESTMENT_MAS_DEFAULT_EMBEDDING_PROVIDER="local_hash"
export INVESTMENT_MAS_ENABLE_FILING_INGESTION="true"
export INVESTMENT_MAS_ENABLE_MARKET_US="true"
export INVESTMENT_MAS_ENABLE_MARKET_HK="true"
export INVESTMENT_MAS_ENABLE_MARKET_CN="true"
export INVESTMENT_MAS_DEFAULT_FILING_LOOKBACK_YEARS="5"
export INVESTMENT_MAS_DEFAULT_CHUNK_SIZE="1200"
export INVESTMENT_MAS_DEFAULT_CHUNK_OVERLAP="180"
```

When `VECTOR_STORE_TYPE=none`, the vector store is bypassed entirely and `RetrievalService` returns empty results, causing `CompanyProfileRAGService` to fall back to keyword-based chunk retrieval from PostgreSQL `filing_chunks`. This is useful when the ChromaDB SQLite file is too large or locked.

Filing ingestion is decoupled from the research workflow hot path. The v3 `DataAvailabilityChecker` detects existing filings and `CompanyProfiler` uses `skip_ingestion=True` by default. To ingest filings ahead of time, use `POST /research/prewarm`. The `DataFetcher` node only downloads filings that are listed in `fetch_needed` from the availability check.

### XBRL Header Cleanup

US SEC EDGAR `.txt` annual reports may contain XBRL metadata headers. Use the remediation script:

```bash
python scripts/remediate_xbrl_chunks.py --dry-run    # Preview only
python scripts/remediate_xbrl_chunks.py               # Fix all
python scripts/remediate_xbrl_chunks.py --ticker NVDA  # Fix specific ticker
python scripts/remediate_xbrl_chunks.py --filing-id 37 # Fix specific filing
```

### Rebuild Index

1. Delete `data/vector_store`
2. Optionally delete `data/company_docs` to re-download
3. Run `POST /research/run` — the system rebuilds the index automatically

## Seed Demo Data

```bash
python scripts/seed_demo_data.py                       # Full demo + local financial import
python scripts/seed_demo_data.py --skip-demo           # Local financial import only
python scripts/seed_demo_data.py --skip-local-financials  # Demo data only
python scripts/seed_demo_data.py --limit 20            # Limit local import count
```

## Start the Server

Always start the API with the project virtualenv. Do not use bare `uvicorn`
from conda/base, because filing RAG requires the `chromadb` package installed in
the active Python runtime.

Recommended:

```bash
cd /Users/jacky/Ai-code/investment-mas
PORT=8000 ./scripts/run_api.sh
```

Use another port if needed:

```bash
PORT=8001 ./scripts/run_api.sh
```

Equivalent explicit command:

```bash
.venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Runtime check:

```bash
.venv/bin/python scripts/check_runtime.py
```

Expected output should include:

```json
{
  "chromadb_available": true,
  "uvicorn_available": true,
  "app_available": true
}
```

Do not start with this command:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On this machine, bare `uvicorn` may resolve to:

```text
/Users/jacky/miniconda3/bin/python
```

That Python runtime does not have `chromadb`, so the application will fail fast
with:

```text
RuntimeError: ChromaDB is required for filing RAG but is not installed in the active Python runtime
```

## API Endpoints

### Run Research

```bash
curl -X POST http://127.0.0.1:8000/research/run \
  -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","company_name":"NVIDIA"}'
```

### Stream Research (SSE)

```bash
curl http://127.0.0.1:8000/research/stream?ticker=NVDA&company_name=NVIDIA
```

SSE events: `started` → `progress` (per node: started/completed) → `result` → `done`.

### Get Latest Report

```bash
curl http://127.0.0.1:8000/research/NVDA/latest
```

### Get Research Plan

```bash
curl http://127.0.0.1:8000/research/NVDA/plan
```

### Download Report as Word

```bash
curl -O http://127.0.0.1:8000/research/NVDA/report.docx
```

### Interactive Endpoints

- `POST /research/followup` — Evidence-grounded follow-up Q&A with citation and confidence
- `POST /research/feedback` — Section-level feedback (too_shallow / factual_error / missing_info), triggers refinement
- `POST /research/chat` — Lightweight Q&A grounded in the latest report JSON

### Optimization Endpoints

```bash
# Prewarm: ingest filings in the background so the next research run skips ingestion
curl -X POST http://127.0.0.1:8000/research/prewarm \
  -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","company_name":"NVIDIA"}'

# Refresh peers: re-discover peer candidates from all sources
curl -X POST http://127.0.0.1:8000/research/refresh-peers \
  -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","company_name":"NVIDIA"}'

# Rerun section: refine a single report section without full workflow rerun
curl -X POST http://127.0.0.1:8000/research/rerun-section \
  -H "Content-Type: application/json" \
  -d '{"ticker":"NVDA","section_name":"business_analysis","feedback_text":"Please provide deeper analysis."}'
```

## Agent Design

### V3 Expert-Led Planning + Targeted Execution (11 nodes)

The v3 workflow replaces the v2 staged parallel flow (13 nodes) with a plan-driven architecture. The key insight: let an LLM research director plan the investigation scope first, then only fetch and analyze what's needed.

```
Stage 1: ResearchPlanner           → LLM generates research_plan (scope, peers, data needs)
Stage 2: DataAvailabilityChecker   → Deterministic: checks what data exists locally
Stage 3: DataFetcher               → Only fetches data listed in fetch_needed
Stage 4: CompanyProfiler           → Plan-aware profile (skip_ingestion=True)
Stage 5: 4 parallel specialists    → Each reads research_plan.sections for skip/focus/depth
           ├── BusinessAnalyst     →   Business + culture/governance (absorbs CultureAnalyst)
           ├── NewsAnalyst         →   News + events (respects cache freshness)
           ├── FinancialAnalyst    →   Financial analysis + red flags + Yahoo Finance peer comparison
           └── PeerAnalyst         →   Peer classification + Yahoo Finance metric snapshots
Stage 6: ValuationAgent → RiskAnalyst  → Sequential: depends on specialist outputs
Stage 7: SynthesisReviewer         → Quality gate + LLM report synthesis
```

#### Node changes from v2 → v3

| v2 Node | Action | v3 Node |
|---------|--------|---------|
| ContextLoader | Removed → split into DataAvailabilityChecker | DataAvailabilityChecker |
| FilingCoverageChecker | Removed → coverage check moved to DataAvailabilityChecker | — |
| CultureAnalyst | Removed → merged into BusinessAnalyst | — |
| PeerFilingIngester | Removed → functionality moved to DataFetcher | DataFetcher |
| NewsCollector | Renamed | NewsAnalyst |
| PeerAnalyzer | Renamed | PeerAnalyst |
| InvestmentExpert | Split into ResearchPlanner (start) + SynthesisReviewer (end) | ResearchPlanner + SynthesisReviewer |
| ReportCoverageChecker | Removed → quality gate merged into SynthesisReviewer | — |
| — | New | ResearchPlanner |
| — | New | DataAvailabilityChecker |
| — | New | DataFetcher |
| — | New | SynthesisReviewer |

### ResearchPlanner

LLM node that produces a structured research plan:
- `sections`: per-section config (priority, focus_areas, skip, depth)
- `peer_candidates`: 1-3 direct industry peers with rationale (must be same sector / similar business model; capped at 3, validated post-LLM)
- `data_requirements`: what data to fetch (target filings, peer filings, news)
- `research_questions`: 3-5 key questions the report should answer

Fallback when LLM is unavailable: `PeerIntelligenceService.discover_top_peers_fast()` + default section config.

### DataFetcher

Only fetches data listed in `fetch_needed` from the availability check:
- Target company filings: full pipeline with vector store
- Peer filings: lightweight pipeline with `NullVectorStoreProvider` (chunks stored in PostgreSQL only, skips ChromaDB)
- News: delegates to `NewsService`

### SynthesisReviewer

Two-phase node replacing InvestmentExpert + ReportCoverageChecker:
- **Phase 1 (deterministic)**: `ReportCoverageService.check()` — validates section completeness, conglomerate consistency, valuation/risk content
- **Phase 2 (LLM)**: `ReportService.build()` — generates Markdown + JSON + rating; embeds research_questions into report_json

### Peer Intelligence Pipeline

Peer candidates are discovered from multiple sources in priority order:

1. **ResearchPlanner** (v3) — LLM selects 1-3 direct industry peers with rationale (post-validated, capped at 3)
2. **Curated mappings** (`CURATED_PEERS` in `company_info_provider.py`) — known industry peers
3. **Sector clustering** — profile text matched against keyword-to-sector map → `SECTOR_PEER_MAP`
4. **Web provider** — `WebCompanyInfoProvider.get_peers()`
5. **Filing evidence** — competition section mentions matched against known aliases

Candidates are deduplicated, merged (highest similarity score wins), and capped at 12 for discovery. For financial comparison, only the plan's peer_candidates (max 3) are used.

### Peer Financial Comparison

Peer comparison uses **Yahoo Finance TTM data** (`yfinance`) as a single consistent source for both the target company and all peers. This avoids mixing annual report data (varying fiscal years) with TTM data.

- `FinancialAnalyst.compare_peers()` fetches company + peers from Yahoo Finance in one batch
- `PeerAnalyst` fetches peer metric snapshots from Yahoo Finance (with local data fallback)
- Revenue displayed in **millions with currency** (e.g., `393,833M CNY`, `742,776M USD`)
- Target company shown **first in bold** in the comparison table
- Company names used instead of tickers for readability
- Dual-listed ticker aliases supported (e.g., HK.01138 → HK.09988 for Alibaba)

### Peer Financial Metrics

Peer metrics are loaded in priority order:
1. **Local financial data** — csv/xlsx/json files in `data/finance_report_web`
2. **Filing chunk extraction** — when local data is unavailable, PeerAnalyst extracts revenue, margins, ROE/ROIC from peer annual report filing chunks using regex pattern matching on the ingested text

### Section Cache Strategy

After each research run, cacheable sections (`company_profile`, `business_analysis`, `financial_summary`, `peer_analysis`, `risk_assessment`, etc.) are stored under `_section_cache` in `report_json`. On the next run, `DataAvailabilityChecker` loads these cached sections and pre-populates state, so downstream nodes can skip work when cached data is fresh (7-day expiry).

### Vector Store Configuration

Two vector store implementations:

- **ChromaVectorStoreProvider** (`VECTOR_STORE_TYPE=chroma`): persistent ChromaDB with 64-dim local hash embeddings. Used for filing chunk semantic search.
- **NullVectorStoreProvider** (`VECTOR_STORE_TYPE=none`): no-op provider that skips all vector operations. Used by `DataFetcher` for peer filings, and as a global fallback when ChromaDB is unavailable or the SQLite file is too large.

When vector search is unavailable, `CompanyProfileRAGService` falls back to keyword-based chunk retrieval from PostgreSQL `filing_chunks` table.

### Retrieval Policy

Each analysis area has a configured source priority (`primary` → `secondary` → `evidence_only`):

| Area | Primary | Secondary | Evidence Only |
|------|---------|-----------|---------------|
| company_profile | filing_rag | web_provider | — |
| peer_analysis | peer_candidates, peer_metrics, sector_clustering | web_provider | filing_rag |
| financial_analysis | structured_db, local_financial_dir | database_fallback | filing_rag |
| risk_analysis | filing_risk_factors, structured_red_flags | news_events, web_updates | — |

## Testing

```bash
pytest
```

Key test files:
- `test_v3_workflow.py` — v3 workflow integration tests (plan-driven nodes, specialist skip/depth)
- `test_optimization_refactor.py` — peer intelligence, section caching, staging
- `test_research_runner.py` — research runner with sequential execution
- `test_nvidia_retrieval_regression.py` — 8 regression tests for US filing retrieval

## Docker

```bash
cp .env.example .env
docker compose up --build
docker compose exec app alembic upgrade head
docker compose exec app python scripts/seed_demo_data.py
```

## ECS Deployment

Deployment scripts are in `deploy/ecs/`:

```bash
cd deploy/ecs
cp .env.ecs.example .env.ecs
./deploy_to_ecs.sh
```

## Future Extensions

- Semantic embedding provider (e.g., `text2vec-base-chinese` or OpenAI embedding) to replace `local_hash`
- Expand news scraping to full-text financial sites and IR pages
- Earnings call / announcement parsing as independent provider
- Migrate `RiskService` rules to YAML/JSON config
- Strict JSON schema validation and audit logging for DashScope outputs
- Vector-DB sync validation: `vector_status` field + periodic check script
- LLM-driven peer candidate generation as additional source in PeerIntelligenceService
- Hybrid retrieval with reranker for follow-up QA quality improvement
- Telemetry / tracing for per-node latency profiling
- ChromaDB SQLite compaction / rebuild automation for large vector stores
