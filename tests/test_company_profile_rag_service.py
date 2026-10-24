"""Tests for document-enhanced company profiling."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.db.models import Company
from app.providers.document_discovery_provider import WebDocumentDiscoveryProvider
from app.providers.document_parser import HybridDocumentParser
from app.services.company_knowledge_retriever import CompanyKnowledgeRetriever
from app.services.company_profile_rag_service import CompanyProfileRAGService
from app.services.company_profile_service import CompanyProfileService


def test_document_parser_parses_html_into_standard_chunks(tmp_path: Path) -> None:
    html_path = tmp_path / "report.html"
    html_path.write_text(
        """
        <html><body>
        <h1>Business Overview</h1>
        <p>Company sells cloud software to enterprise customers in China and overseas.</p>
        <h2>Risk Factors</h2>
        <p>Competition is intense and customer concentration remains high.</p>
        </body></html>
        """,
        encoding="utf-8",
    )
    parser = HybridDocumentParser(chunk_size=120, chunk_overlap=20)
    chunks = parser.parse(
        {
            "local_path": str(html_path),
            "doc_type": "annual_report",
            "fiscal_year": 2024,
            "ticker": "TEST",
            "company_name": "TestCo",
        }
    )

    assert chunks
    assert chunks[0]["ticker"] == "TEST"
    assert "text" in chunks[0]
    assert "section" in chunks[0]


def test_company_knowledge_retriever_lexical_retrieval(session) -> None:
    company = Company(ticker="TEST", company_name="TestCo", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    session.add(company)
    session.flush()
    from app.db.models import CompanyDocument, DocumentChunk

    doc = CompanyDocument(
        company_id=company.id,
        ticker="TEST",
        company_name="TestCo",
        doc_type="annual_report",
        fiscal_year=2024,
        report_date=None,
        source_name="ir",
        source_url="https://example.com/ar.pdf",
        local_path="/tmp/ar.pdf",
        checksum="x",
        downloaded_at=datetime.now(timezone.utc),
        raw_metadata={},
    )
    session.add(doc)
    session.flush()
    session.add_all(
        [
            DocumentChunk(
                company_document_id=doc.id,
                company_id=company.id,
                ticker="TEST",
                company_name="TestCo",
                doc_type="annual_report",
                fiscal_year=2024,
                section="Business Overview",
                page_number=1,
                chunk_index=1,
                text="Our business model focuses on enterprise cloud software subscriptions.",
                chunk_metadata={},
                created_at=datetime.now(timezone.utc),
            ),
            DocumentChunk(
                company_document_id=doc.id,
                company_id=company.id,
                ticker="TEST",
                company_name="TestCo",
                doc_type="annual_report",
                fiscal_year=2024,
                section="Risk Factors",
                page_number=2,
                chunk_index=2,
                text="Risk factors include competition, customer concentration, and margin pressure.",
                chunk_metadata={},
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    session.flush()

    retriever = CompanyKnowledgeRetriever(vector_backend="memory")
    bundle, warnings = retriever.retrieve(session, company.id, ["business_model", "risk_factors"])

    assert warnings == []
    assert bundle["business_model"]
    assert bundle["risk_factors"]


class _FakeRAGService:
    def build(self, session, company, model=None, skip_ingestion=False):
        return (
            {
                "company_name": company.company_name,
                "ticker": company.ticker,
                "business_model": "Business model profile based on annual report evidence.",
                "revenue_model": "Subscription and service fees.",
                "key_products": ["Cloud platform"],
                "key_customers": ["Enterprise clients"],
                "key_regions": ["China"],
                "market_space": "Enterprise software market still has room for growth.",
                "competition_landscape": "Competition is intense.",
                "competitive_advantages": ["Customer stickiness"],
                "competitive_weaknesses": ["Insufficient internationalization"],
                "risk_factors": ["Intensifying competition"],
                "corporate_culture": "Insufficient information",
                "management_summary": "Management emphasizes prudent operations.",
                "confidence": 0.7,
                "warnings": ["Example warning"],
                "raw_sources": [{"source": "document_rag"}],
            },
            {"competition": [{"text": "Peers include Alpha Corp and Beta Inc."}]},
            ["Example warning"],
        )


class _FakePeerService:
    def discover(self, ticker, company_name, company_profile, evidence_bundle, model=None):
        return (
            [{"ticker": "PEER", "company_name": "PeerCo", "peer_type": "core", "similarity_score": 0.8}],
            ["Peer discovery warning"],
        )


def test_company_profile_service_uses_rag_pipeline_by_default(session) -> None:
    service = CompanyProfileService(rag_service=_FakeRAGService(), peer_service=_FakePeerService())

    profile, peers, company = service.build_profile(session, "TEST", "TestCo")

    assert company is not None
    assert profile["business_model"] == "Business model profile based on annual report evidence."
    assert profile["warnings"]
    assert peers[0]["ticker"] == "PEER"


def test_company_profile_rag_service_json_safe_converts_dates() -> None:
    service = CompanyProfileRAGService()

    payload = service._json_safe(  # type: ignore[attr-defined]
        {
            "report_date": datetime(2026, 4, 22, tzinfo=timezone.utc).date(),
            "downloaded_at": datetime(2026, 4, 22, tzinfo=timezone.utc),
            "nested": [Path("/tmp/demo.pdf")],
        }
    )

    assert payload["report_date"] == "2026-04-22"
    assert payload["downloaded_at"].startswith("2026-04-22T")
    assert payload["nested"] == ["/tmp/demo.pdf"]


def test_company_profile_rag_service_falls_back_when_llm_times_out(session) -> None:
    company = Company(ticker="US.META", company_name="Meta Platforms", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    session.add(company)
    session.flush()

    class _FakeFilingServiceForTimeout:
        def ingest_company_filings(self, session, company, market=None, years=None):
            return [type("Filing", (), {"id": 1, "filing_type": "annual_report", "fiscal_year": 2025, "source_name": "sec_edgar_direct_filing", "file_url": "https://www.sec.gov/example.htm"})()], []

    class _FakeRetrievalForTimeout:
        def search(self, query, filters=None, top_k=4):
            return [{"text": "Meta generates advertising revenue from its family of apps.", "filing_id": 1, "section": "Business", "page_number": 3, "metadata": {"fiscal_year": 2025}, "score": 0.9}]

    class _TimeoutLLM:
        available = True

        def synthesize_company_profile(self, **kwargs):
            raise TimeoutError("The read operation timed out")

    service = CompanyProfileRAGService(
        filing_service=_FakeFilingServiceForTimeout(),
        retrieval_service=_FakeRetrievalForTimeout(),
        llm_service=_TimeoutLLM(),
    )

    profile, evidence_bundle, warnings = service.build(session, company)

    assert profile is not None
    assert profile["business_model"] != ""
    assert "falling back to rule-based summary" in " ".join(warnings).lower()
    assert evidence_bundle["business_model"]


def test_document_discovery_provider_builds_futu_slugs() -> None:
    provider = WebDocumentDiscoveryProvider()

    assert provider._futu_stock_slug("HK.00700") == "00700-HK"  # type: ignore[attr-defined]
    assert provider._futu_stock_slug("PDD") == "PDD-US"  # type: ignore[attr-defined]


def test_document_discovery_provider_filters_non_ir_docs() -> None:
    provider = WebDocumentDiscoveryProvider()

    candidate = provider._candidate_from_link(  # type: ignore[attr-defined]
        "https://docs.nvidia.com/learn-openusd/latest/index.html",
        "OpenUSD 文档",
        "official_site",
        "NVDA",
        "NVIDIA",
    )
    assert candidate is None


def test_document_discovery_provider_accepts_futu_announcement_links() -> None:
    provider = WebDocumentDiscoveryProvider()

    candidate = provider._candidate_from_link(  # type: ignore[attr-defined]
        "https://www.futunn.com/stock/00700-HK/announcement/earnings-release-2024.pdf",
        "2024 业绩公告",
        "futu_announcement",
        "HK.00700",
        "Tencent",
    )
    assert candidate is not None
    assert candidate.doc_type == "ir_material"


def test_document_discovery_provider_rejects_futu_news_posts_as_documents() -> None:
    provider = WebDocumentDiscoveryProvider()

    candidate = provider._candidate_from_link(  # type: ignore[attr-defined]
        "https://news.futunn.com/post/71663363/arete-research-upgrades-pdd-holdings-pddus-to-buy-rating-cuts",
        "Arete Research upgrades PDD Holdings",
        "futu_institutional_ratings",
        "PDD",
        "PDD Holdings",
    )

    assert candidate is None


def test_document_discovery_provider_adds_cninfo_and_sec_entry_pages() -> None:
    provider = WebDocumentDiscoveryProvider()

    mainland_pages = provider._entry_pages("SH.601857", {"company_name": "PetroChina"})  # type: ignore[attr-defined]
    us_pages = provider._entry_pages("PDD", {"company_name": "PDD Holdings"})  # type: ignore[attr-defined]

    mainland_urls = [url for _, url in mainland_pages]
    us_urls = [url for _, url in us_pages]

    assert any("cninfo.com.cn/new/disclosure/stock?stockCode=601857" in url for url in mainland_urls)
    assert any("sec.gov/cgi-bin/browse-edgar" in url and "PDD+Holdings" in url for url in us_urls)


class _FakeFilingService:
    def ingest_company_filings(self, session, company, market=None, years=None):
        return [type("Filing", (), {"id": 11, "filing_type": "annual_report", "fiscal_year": 2024, "source_name": "sec_edgar_search", "file_url": "https://example.com/20f"})()], []


class _FakeRetrievalService:
    def search(self, query, filters=None, top_k=5):
        return [
            {
                "text": "公司通过电商平台和跨境业务变现。",
                "score": 0.91,
                "filing_id": 11,
                "section": "Business Overview",
                "page_number": 3,
                "metadata": {"fiscal_year": 2024},
            }
        ]


def test_company_profile_rag_service_builds_profile_from_filing_evidence(session) -> None:
    company = Company(ticker="PDD", company_name="PDD Holdings", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    session.add(company)
    session.flush()

    service = CompanyProfileRAGService(
        filing_service=_FakeFilingService(),
        retrieval_service=_FakeRetrievalService(),
        llm_service=type("LLM", (), {"available": False})(),
    )

    profile, evidence_bundle, warnings = service.build(session, company)

    assert profile is not None
    assert profile["raw_sources"][0]["source"] == "filing"
    assert evidence_bundle["business_model"]
    assert warnings
    assert "falling back to rule-based summary" in warnings[0].lower()
