"""Regression tests for NVIDIA-class US filing retrieval pipeline.

Ensures the vector store filter, keyword fallback, filing coverage check,
and profile RAG pipeline all produce non-empty evidence for a typical
US SEC annual report (10-K style) chunk set.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.models import Company
from app.db.models.filing import Filing, FilingChunk
from app.providers.vector_store_provider import ChromaVectorStoreProvider
from app.services.business_analysis_service import BusinessAnalysisService
from app.services.company_profile_rag_service import CompanyProfileRAGService
from app.services.filing_coverage_service import FilingCoverageService


NVIDIA_CHUNKS = [
    {
        "section": "Business",
        "page_number": 3,
        "text": (
            "NVIDIA pioneered accelerated computing to help solve the most computationally "
            "intensive problems. Our two reportable segments are Compute & Networking and Graphics. "
            "Data Center revenue includes GPU and networking solutions for AI, HPC, and cloud computing."
        ),
    },
    {
        "section": "Business",
        "page_number": 4,
        "text": (
            "Our products include GeForce GPUs for gaming, NVIDIA RTX for professional workstations, "
            "and data center platforms including A100 and H100 Tensor Core GPUs, DGX systems, "
            "and networking products including Mellanox InfiniBand and Ethernet."
        ),
    },
    {
        "section": "Business - Competition",
        "page_number": 12,
        "text": (
            "The market for our products is intensely competitive. We compete with AMD, Intel, "
            "and others in GPU, CPU, DPU, and networking. Our competitive advantages include "
            "CUDA ecosystem, software platform, and architecture innovation."
        ),
    },
    {
        "section": "Risk Factors",
        "page_number": 18,
        "text": (
            "Risk factors include: export controls on sales to China and other restricted regions, "
            "supply chain concentration in Taiwan, intense competition, customer concentration in "
            "hyperscale data center operators, and geopolitical uncertainties."
        ),
    },
    {
        "section": "Management Discussion and Analysis",
        "page_number": 30,
        "text": (
            "Revenue for fiscal year 2025 increased 122% year-over-year driven by Data Center "
            "segment growth. Gross margin expanded to 73.0%. We returned capital to shareholders "
            "through share repurchases of $9.7 billion."
        ),
    },
    {
        "section": "Executive Officers",
        "page_number": 55,
        "text": (
            "Jensen Huang, age 61, co-founded NVIDIA in 1993 and has served as President, CEO, "
            "and a member of the Board of Directors since inception. Colette Kress has served as "
            "Executive Vice President and CFO since 2013."
        ),
    },
    {
        "section": "Executive Compensation",
        "page_number": 60,
        "text": (
            "Our executive compensation program includes base salary, annual cash incentive, "
            "and long-term equity awards. For fiscal 2025, the CEO received total compensation "
            "of $34.2 million, including stock awards tied to TSR performance."
        ),
    },
    {
        "section": "Revenue by Geography",
        "page_number": 35,
        "text": (
            "Revenue by region: United States 44%, Taiwan 21%, China 13%, Other 22%. "
            "International sales are subject to export controls and geopolitical risk."
        ),
    },
]


def _seed_nvidia(session: Session) -> tuple[Company, list[FilingChunk]]:
    """Insert a Company, one Filing, and representative 10-K chunks."""
    company = Company(
        ticker="US.NVDA",
        company_name="NVIDIA Corporation",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(company)
    session.flush()

    filing = Filing(
        company_id=company.id,
        ticker="US.NVDA",
        company_name="NVIDIA Corporation",
        market="us",
        filing_type="annual_report",
        fiscal_year=2025,
        title="Annual Report on Form 10-K FY2025",
        source_name="sec_edgar_direct_filing",
        file_url="https://www.sec.gov/Archives/edgar/data/1045810/10-K-2025.htm",
        local_path="/tmp/nvda_10k.htm",
        download_status="downloaded",
        parse_status="parsed",
        metadata_json={"filing_type": "annual_report"},
    )
    session.add(filing)
    session.flush()

    chunks: list[FilingChunk] = []
    for idx, spec in enumerate(NVIDIA_CHUNKS):
        chunk = FilingChunk(
            filing_id=filing.id,
            company_id=company.id,
            chunk_index=idx,
            section=spec["section"],
            page_number=spec["page_number"],
            text=spec["text"],
            metadata_json={"filing_type": "annual_report", "fiscal_year": 2025, "company_id": company.id, "filing_id": filing.id},
            created_at=datetime.now(timezone.utc),
        )
        chunks.append(chunk)
    session.add_all(chunks)
    session.flush()
    session.commit()
    return company, chunks


# ---------------------------------------------------------------------------
# 1. ChromaDB _build_where regression
# ---------------------------------------------------------------------------

def test_chroma_build_where_wraps_multi_key_filters() -> None:
    where = ChromaVectorStoreProvider._build_where({"company_id": 24, "filing_type": "annual_report"})
    assert where is not None
    assert "$and" in where
    assert len(where["$and"]) == 2


def test_chroma_build_where_single_key_passthrough() -> None:
    where = ChromaVectorStoreProvider._build_where({"company_id": 24})
    assert where == {"company_id": 24}


def test_chroma_build_where_none_on_empty() -> None:
    assert ChromaVectorStoreProvider._build_where(None) is None
    assert ChromaVectorStoreProvider._build_where({}) is None


# ---------------------------------------------------------------------------
# 2. Keyword fallback retrieval — no vector store needed
# ---------------------------------------------------------------------------

def test_keyword_fallback_retrieves_nvidia_evidence(session: Session) -> None:
    company, _ = _seed_nvidia(session)

    class _NoOpRetrieval:
        def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
            return []

    service = CompanyProfileRAGService(
        filing_service=type("FS", (), {"ingest_company_filings": staticmethod(lambda *a, **kw: ([], []))})(),
        retrieval_service=_NoOpRetrieval(),
        llm_service=type("LLM", (), {"available": False})(),
    )

    bundle = service._retrieve_evidence_bundle(session, company, top_k=4)

    assert bundle["business_model"], "business_model evidence must not be empty"
    assert bundle["competition"], "competition evidence must not be empty"
    assert bundle["risk_factors"], "risk_factors evidence must not be empty"
    assert bundle["management"], "management evidence must not be empty"
    assert bundle["key_products"], "key_products evidence must not be empty"
    assert bundle["key_regions"], "key_regions evidence must not be empty"


def test_business_analysis_keyword_fallback_uses_local_filing_chunks(session: Session) -> None:
    company, _ = _seed_nvidia(session)

    class _NoOpRetrieval:
        def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
            return []

    result = BusinessAnalysisService(
        retrieval_service=_NoOpRetrieval(),
        llm_service=type("LLM", (), {"available": False})(),
    ).analyze(
        session=session,
        ticker=company.ticker,
        company_name=company.company_name,
        company_profile={"business_model": "Insufficient information"},
        filing_coverage={"covered": {"business_model": {}, "competition": {}, "segments": {}}},
    )

    core = result["business_analysis"]["core_judgement"]
    competition = result["business_analysis"]["competition"]["core_judgement"]
    assert "Data Center revenue" in core or "reportable segments" in core
    assert "competitive" in competition.lower()
    assert "Insufficient information" not in core


def test_business_analysis_rejects_insufficient_llm_when_local_evidence_exists(session: Session) -> None:
    company, _ = _seed_nvidia(session)

    class _NoOpRetrieval:
        def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
            return []

    class _InsufficientLLM:
        available = True

        def chat_json(self, messages: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
            return {
                "business_analysis": {
                    "core_judgement": "Insufficient information to assess the business model, market position, or competitive dynamics.",
                    "business_model_detail": "",
                    "market_space": {"core_judgement": "Insufficient information", "market_drivers": [], "market_size_estimate": "", "open_questions": []},
                    "competition": {"core_judgement": "Insufficient information", "key_competitors": [], "competitive_advantages": [], "competitive_weaknesses": [], "open_questions": []},
                    "supporting_evidence_refs": [],
                    "open_questions": [],
                },
                "segment_analysis": {"is_group_company": False, "segments": [], "segment_level_open_questions": [], "supporting_evidence_refs": []},
            }

    result = BusinessAnalysisService(
        retrieval_service=_NoOpRetrieval(),
        llm_service=_InsufficientLLM(),
    ).analyze(
        session=session,
        ticker=company.ticker,
        company_name=company.company_name,
        company_profile={"business_model": "Insufficient information"},
        filing_coverage={"covered": {"business_model": {}, "competition": {}, "segments": {}}},
    )

    core = result["business_analysis"]["core_judgement"]
    assert "Insufficient information to assess" not in core
    assert result["warnings"]
    assert any("LLM returned insufficient analysis" in item for item in result["warnings"])


# ---------------------------------------------------------------------------
# 3. FilingCoverageService with keyword-match evidence
# ---------------------------------------------------------------------------

def test_filing_coverage_service_detects_nvidia_topics(session: Session) -> None:
    company, _ = _seed_nvidia(session)

    class _KeywordRetrieval:
        def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
            results = []
            query_lower = query.lower()
            for spec in NVIDIA_CHUNKS:
                haystack = f"{spec['section']} {spec['text']}".lower()
                kw_hits = sum(1 for word in query_lower.split() if len(word) > 3 and word in haystack)
                if kw_hits > 0:
                    results.append({"text": spec["text"], "score": 0.1 * kw_hits, "filing_id": 1, "section": spec["section"], "page_number": spec["page_number"], "metadata": {}})
            results.sort(key=lambda r: r["score"], reverse=True)
            return results[:top_k]

    service = FilingCoverageService(retrieval_service=_KeywordRetrieval())
    result = service.check(session, "US.NVDA", {})

    assert result["overall_coverage"] >= 0.5, f"Expected >= 50% coverage but got {result['overall_coverage']}"
    assert "business_model" in result["covered"] or "business_model" not in result["missing"], \
        "business_model should be covered for NVIDIA"


# ---------------------------------------------------------------------------
# 4. Full RAG pipeline — profile fields must not be "Insufficient information"
# ---------------------------------------------------------------------------

def test_rag_profile_nvidia_fields_populated(session: Session) -> None:
    company, _ = _seed_nvidia(session)

    class _StubFilingService:
        def ingest_company_filings(self, session, company, market=None, years=None):
            return [type("F", (), {"id": 1, "filing_type": "annual_report", "fiscal_year": 2025, "source_name": "sec_edgar_direct_filing", "file_url": "https://sec.gov/10k"})()], []

    class _NoOpRetrieval:
        def search(self, query, filters=None, top_k=5):
            return []

    service = CompanyProfileRAGService(
        filing_service=_StubFilingService(),
        retrieval_service=_NoOpRetrieval(),
        llm_service=type("LLM", (), {"available": False})(),
    )

    profile, evidence_bundle, warnings = service.build(session, company)

    assert profile is not None, "profile must not be None"

    critical_fields = ["business_model", "competition_landscape", "management_summary", "risk_factors"]
    for field in critical_fields:
        value = profile.get(field, "")
        if isinstance(value, str):
            assert value != "Insufficient information", f"{field} must not be 'Insufficient information' for NVIDIA"
            assert value.strip(), f"{field} must not be empty for NVIDIA"
        elif isinstance(value, list):
            assert len(value) > 0, f"{field} must not be empty list for NVIDIA"


# ---------------------------------------------------------------------------
# 5. Bilingual query templates contain English terms
# ---------------------------------------------------------------------------

def test_topic_queries_contain_english_terms() -> None:
    from app.services.company_profile_rag_service import TOPIC_QUERIES

    for topic, query in TOPIC_QUERIES.items():
        has_ascii = any(c.isascii() and c.isalpha() for c in query)
        assert has_ascii, f"TOPIC_QUERIES['{topic}'] should contain English terms for US filing compatibility"


def test_filing_coverage_queries_contain_english_terms() -> None:
    from app.services.filing_coverage_service import TOPIC_QUERIES

    for topic, query in TOPIC_QUERIES.items():
        has_ascii = any(c.isascii() and c.isalpha() for c in query)
        assert has_ascii, f"FilingCoverage TOPIC_QUERIES['{topic}'] should contain English terms"
