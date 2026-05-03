"""Regression tests for a company that does not exist before the run."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.repositories.company_repository import get_company_by_ticker
from app.db.repositories.filing_repository import get_filing_chunks, get_filings
from app.providers.filing_discovery_provider import FilingDiscoveryProvider
from app.providers.filing_download_provider import FilingDownloadProvider
from app.providers.vector_store_provider import VectorStoreProvider
from app.services.company_profile_rag_service import CompanyProfileRAGService
from app.services.company_profile_service import CompanyProfileService
from app.services.document_storage_service import DocumentStorageService
from app.services.filing_coverage_service import FilingCoverageService
from app.services.filing_service import FilingService
from app.services.retrieval_service import RetrievalService


class _SingleAnnualReportDiscovery(FilingDiscoveryProvider):
    """Return one real annual-report candidate for the new company."""

    def discover_filings(self, ticker: str, company_name: str, market: str, years: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "ticker": ticker,
                "company_name": company_name,
                "market": market,
                "filing_type": "annual_report",
                "period_type": "annual",
                "fiscal_year": 2025,
                "fiscal_period": "FY",
                "report_date": "2026-04-01",
                "published_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
                "title": "Annual Report 2025",
                "source_name": "unit_test_discovery",
                "source_url": "https://example.com/new-company/annual-report",
                "file_url": "https://example.com/new-company/annual-report-2025.htm",
                "file_type": "html",
            }
        ]


class _StoredHtmlDownload(FilingDownloadProvider):
    """Persist a deterministic annual report through the real storage layer."""

    def __init__(self, base_dir: Path) -> None:
        self.storage = DocumentStorageService(base_dir=str(base_dir))

    def download_filing(self, filing_meta: dict[str, Any]) -> dict[str, Any]:
        html = """
        <html><body>
        <h1>Item 1. Business</h1>
        <p>NewPipe operates a subscription software platform for enterprise workflow automation.</p>
        <p>The company generates revenue from subscriptions, usage-based fees, and professional services.</p>
        <h2>Products and Services</h2>
        <p>Its key products include workflow cloud, analytics modules, and developer tools.</p>
        <h2>Competition</h2>
        <p>The market is competitive and includes platform vendors and specialized automation providers.</p>
        <h2>Management and Governance</h2>
        <p>The board of directors oversees executive officers, governance, and compensation programs.</p>
        <h2>Risk Factors</h2>
        <p>Risk factors include customer concentration, regulation, cybersecurity, and demand uncertainty.</p>
        <h2>Segments</h2>
        <p>Segment revenue is reported by subscription software, services, and geographic regions.</p>
        </body></html>
        """
        result = self.storage.store_download(
            filing_meta=filing_meta,
            content=html.encode("utf-8"),
            mime_type="text/html; charset=utf-8",
        )
        return {**filing_meta, **result, "status": "downloaded"}


class _MemoryVectorStore(VectorStoreProvider):
    """Small in-memory vector store used to prove upsert/search wiring."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        self.rows.extend(chunks)

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        normalized_filters = {k: v for k, v in (filters or {}).items() if not k.startswith("__")}
        query_terms = [term.lower() for term in query.replace(",", " ").split() if len(term) > 3]
        results: list[dict[str, Any]] = []
        for row in self.rows:
            metadata = row.get("metadata") or {}
            if any(metadata.get(key) != value for key, value in normalized_filters.items()):
                continue
            text = str(row.get("text") or "")
            score = sum(1 for term in query_terms if term in text.lower())
            if score <= 0:
                continue
            results.append(
                {
                    "id": row.get("vector_id"),
                    "text": text,
                    "score": float(score),
                    "filing_id": metadata.get("filing_id"),
                    "section": metadata.get("section"),
                    "page_number": metadata.get("page_number"),
                    "metadata": metadata,
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]


class _ProfileLLM:
    """LLM test double that only succeeds when evidence was actually retrieved."""

    available = True

    def synthesize_company_profile(self, ticker: str, company_name: str, raw_context: dict[str, Any], model: str | None = None) -> dict[str, Any]:
        evidence = raw_context["evidence_bundle"]
        assert evidence["business_model"], "business evidence must come from downloaded filing chunks"
        assert evidence["risk_factors"], "risk evidence must come from downloaded filing chunks"
        return {
            "company_name": company_name,
            "ticker": ticker,
            "business_model": "公司运营企业工作流自动化订阅软件平台。",
            "revenue_model": "收入来自订阅、用量计费和专业服务。",
            "key_products": ["workflow cloud", "analytics modules", "developer tools"],
            "key_customers": ["enterprise clients"],
            "key_regions": ["信息不足"],
            "market_space": "信息不足",
            "competition_landscape": "市场竞争激烈，包括平台型厂商和垂直自动化供应商。",
            "competitive_advantages": [],
            "competitive_weaknesses": [],
            "risk_factors": ["客户集中", "监管", "网络安全", "需求不确定性"],
            "corporate_culture": "信息不足",
            "management_summary": "董事会监督高管、治理和薪酬计划。",
            "confidence": 0.8,
        }


class _PeerService:
    """Peer discovery test double."""

    def discover(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        evidence_bundle: dict[str, list[dict[str, Any]]],
        model: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return [
            {
                "ticker": "US.PEERX",
                "company_name": "PeerX",
                "peer_type": "valuation",
                "similarity_score": 0.7,
                "rationale": "同属企业软件估值可比公司。",
            }
        ], []


def test_new_company_ingests_downloads_indexes_and_profiles(session: Session, tmp_path: Path) -> None:
    """A missing company should be created, filed, indexed, retrieved, and profiled."""

    ticker = "US.NEWP"
    assert get_company_by_ticker(session, ticker) is None

    vector_store = _MemoryVectorStore()
    filing_service = FilingService(
        discovery_provider=_SingleAnnualReportDiscovery(),
        download_provider=_StoredHtmlDownload(tmp_path),
        vector_store=vector_store,
    )
    retrieval_service = RetrievalService(vector_store=vector_store)
    rag_service = CompanyProfileRAGService(
        filing_service=filing_service,
        retrieval_service=retrieval_service,
        llm_service=_ProfileLLM(),  # type: ignore[arg-type]
    )
    service = CompanyProfileService(
        rag_service=rag_service,
        peer_service=_PeerService(),  # type: ignore[arg-type]
    )

    profile, peers, company = service.build_profile(session, ticker, "NewPipe Systems")

    assert company is not None
    assert get_company_by_ticker(session, ticker) is not None
    assert profile["business_model"] == "公司运营企业工作流自动化订阅软件平台。"
    assert peers and peers[0]["ticker"] == "US.PEERX"
    assert len(vector_store.rows) > 0

    filings = get_filings(session, company.id)
    assert len(filings) == 1
    filing = filings[0]
    assert filing.download_status == "downloaded"
    assert filing.parse_status == "parsed"
    assert filing.local_path and Path(filing.local_path).suffix == ".txt"
    assert Path(filing.local_path).exists()
    assert (filing.metadata_json or {}).get("raw_local_path")
    assert Path(str((filing.metadata_json or {})["raw_local_path"])).exists()
    assert get_filing_chunks(session, company.id)

    coverage = FilingCoverageService(retrieval_service=retrieval_service).check(session, ticker, profile)
    assert coverage["overall_coverage"] >= 0.5
    assert "business_model" in coverage["covered"]
    assert "risk_factors" in coverage["covered"]
