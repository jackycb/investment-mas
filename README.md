# Investment MAS

An investment research Multi-Agent System built on Python 3.11, LangGraph, FastAPI, PostgreSQL, SQLAlchemy, and Alembic.

- Financial data reads from local directory first
- Public information fetched via web scraping
- Uses Alibaba Cloud DashScope / Qwen for structured analysis, news event enrichment, and comprehensive research report generation
- All LLM prompts, reports, and UI are in English

## Project Structure

```text
investment_mas/
  app/
    api/
      routes/
        health.py
        research.py            # /run, /stream, /chat, /followup, /feedback, /filings, /documents
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
      state.py                 # ResearchState with filing_coverage, business/culture/peer/segment analysis
      workflow.py              # 11-node sequential LangGraph workflow
      nodes/
        company_profiler.py
        filing_coverage_checker.py
        business_analyst.py
        culture_analyst.py
        peer_analyzer.py
        news_collector.py
        financial_analyst.py
        valuation_agent.py
        risk_analyst.py
        investment_expert.py
        report_coverage_checker.py
    providers/
      company_info_provider.py
      embedding_provider.py    # LocalHashEmbeddingProvider
      financial_data_provider.py
      filing_discovery_provider.py
      news_provider.py
      vector_store_provider.py # ChromaDB with $and filter support
    schemas/
    services/
      business_analysis_service.py     # Market space, segments, competition deep-dive
      company_profile_rag_service.py   # Filing RAG with bilingual queries + keyword fallback
      culture_governance_service.py    # Management, compensation, governance
      feedback_refinement_service.py   # Section-level report refinement
      filing_coverage_service.py       # Topic evidence coverage check
      followup_qa_service.py           # Evidence-grounded follow-up QA
      peer_analysis_service.py         # Peer classification
      report_coverage_service.py       # Post-report quality gate
      retrieval_service.py             # Vector + keyword filing chunk search
    utils/
      file_discovery.py
      financial_mapping.py
      financial_parser.py
    web/
      index.html               # English web UI
    main.py
  alembic/
    versions/
  docs/
    nvidia_diagnosis_report.md
  scripts/
    seed_demo_data.py
    remediate_xbrl_chunks.py
    check_runtime.py            # Verify active Python runtime dependencies
    run_api.sh                  # Start API with project .venv, avoiding conda/base mismatch
  tests/
    test_nvidia_retrieval_regression.py  # 8 regression tests for US filing retrieval
    test_company_profile_rag_service.py
    test_retrieval_service.py
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

- 11-node sequential LangGraph workflow:
  `CompanyProfiler → FilingCoverageChecker → BusinessAnalyst → CultureAnalyst → PeerAnalyzer → NewsCollector → FinancialAnalyst → ValuationAgent → RiskAnalyst → InvestmentExpert → ReportCoverageChecker`
- Filing knowledge retrieval augmentation (RAG): auto-discover, download, parse SEC/HKEXnews/CNINFO annual reports, chunk and build local vector index for profiling/analysis/risk nodes
- Bilingual query templates: supports both English and Chinese filings (US 10-K, HK annual reports, A-share annual reports)
- Financial data from local directory + database fallback
- News/announcements/public info via web scraping
- Report generation via Alibaba Cloud DashScope / Qwen / DeepSeek
- `CompanyProfiler` uses "Filing RAG evidence + structured candidate retrieval + rule filtering + LLM synthesis"
- `NewsCollector` uses "web search / news feed scraping + dedup + LLM summarization and event extraction"
- Financial data defaults to project directory `data/finance_report_web`
- Auto-downloads missing tickers via `scripts/download_financial_data.py` from Futu
- `scripts/run_value_analysis_by_code.py` integrated as supplemental valuation in `ValuationAgent`
- `scripts/stock_analysis_futu.py` integrated into `FinancialAnalyst` for auto-computing missing gross margin / net margin / ROE / ROIC
- Falls back to PostgreSQL `financial_reports` when local directory is missing
- Interactive research: `/research/followup` (evidence-grounded Q&A), `/research/feedback` (section-level report refinement)
- Database persistence:
  - `companies`, `company_profiles`, `company_segment_profiles`, `peer_mappings`
  - `financial_reports`, `financial_peer_snapshots`
  - `news_articles`, `news_events`
  - `risk_assessments`, `valuation_results`, `research_reports`
  - `filings` / `filing_chunks`
  - `management_profiles` / `incentive_records`
  - `evidence_spans`
  - `report_sessions` / `followup_questions` / `user_feedback`
- Outputs Markdown report and structured JSON research results

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
export INVESTMENT_MAS_DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export INVESTMENT_MAS_DASHSCOPE_MODEL="qwen-plus"
export INVESTMENT_MAS_DASHSCOPE_TEMPERATURE="0.2"
export INVESTMENT_MAS_ENABLE_LLM_REPORT="true"
```

Uses DashScope OpenAI-compatible Chat API (`/chat/completions`). Can also reuse existing DashScope config files:

```bash
export INVESTMENT_MAS_DASHSCOPE_CONFIG_PATH="/path/to/llm_config.json"
```

Configuration priority: JSON config file → `.env` variables → runtime parameters.

Data source strategy:

- Financial data: local `data/finance_report_web` → Futu download → PostgreSQL fallback
- Company profile: curated fallback → Filing RAG → LLM synthesis
- Peers: static mapping → industry tag matching → LLM annotation (explanation only, not generation)
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
7. `VectorStoreProvider` persists chunks to local vector store
8. `RetrievalService` serves queries for `CompanyProfiler` / `RiskAnalyst`

### Local Directories

```bash
data/company_docs/{market}/{ticker}/{filing_type}/{fiscal_year}/
data/vector_store/
```

### Environment Variables

```bash
export INVESTMENT_MAS_DOCUMENT_STORAGE_DIR="/Users/jacky/Ai-code/investment-mas/data/company_docs"
export INVESTMENT_MAS_VECTOR_STORE_TYPE="chroma"
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

Filing ingestion is triggered automatically during `CompanyProfiler`. The system discovers candidates, downloads, parses, chunks, indexes, and retrieves evidence per topic.

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

### Get Latest Report

```bash
curl http://127.0.0.1:8000/research/NVDA/latest
```

### Interactive Endpoints

- `POST /research/followup` — Evidence-grounded follow-up Q&A with citation and confidence
- `POST /research/feedback` — Section-level feedback (too_shallow / factual_error / missing_info), triggers refinement
- `POST /research/chat` — Lightweight Q&A grounded in the latest report JSON

## Agent Design

11-node workflow, each node's role:

- **CompanyProfiler**: Triggers filing ingestion, retrieves filing evidence by topic (bilingual queries + keyword fallback), generates company profile via LLM or fallback rules
- **FilingCoverageChecker**: Checks key topics for sufficient filing evidence, generates coverage report and gap warnings
- **BusinessAnalyst**: Deep-dives into business model, market space, segments, competitive landscape using filing evidence
- **CultureAnalyst**: Analyzes corporate culture, management team, compensation, governance from filings
- **PeerAnalyzer**: Classifies peer candidates (core / secondary / valuation / weakly_related)
- **NewsCollector**: Reads cached news, scrapes Google News RSS / SEC EDGAR, deduplicates, extracts events via rules or LLM
- **FinancialAnalyst**: Reads local financial data first, Futu download fallback, then database fallback
- **ValuationAgent**: Basic valuation + supplemental valuation script
- **RiskAnalyst**: Financial rules + news events + filing evidence for risk factors
- **InvestmentExpert**: LLM comprehensive report or deterministic template fallback
- **ReportCoverageChecker**: Validates report sections for evidence sufficiency

## Testing

```bash
pytest
```

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
- Ticker normalization: unify `{market}.{ticker}` format
- Expand news scraping to full-text financial sites and IR pages
- Earnings call / announcement parsing as independent provider
- Checkpoint / persistence at `workflow.py` graph compilation
- Migrate `RiskService` rules to YAML/JSON config
- Strict JSON schema validation and audit logging for DashScope outputs
- Vector-DB sync validation: `vector_status` field + periodic check script
