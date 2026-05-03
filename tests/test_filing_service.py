"""Tests for filing ingestion repair behavior."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import pytest

from app.db.models import Company, Filing
from app.db.repositories.filing_repository import get_effective_filings, get_filing_chunks_by_filing
from app.services.filing_service import FilingService


class _StaticDiscoveryProvider:
    """Return one deterministic filing metadata item."""

    def __init__(self, file_url: str) -> None:
        self.file_url = file_url

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
                "report_date": "2025-12-31",
                "published_at": "2026-04-30T00:00:00+00:00",
                "title": "2025 Annual Report",
                "source_name": "cninfo_query",
                "source_url": "https://www.cninfo.com.cn/new/fulltextSearch",
                "file_url": self.file_url,
                "file_type": "pdf",
                "metadata_json": {"adapter": "cninfo_query", "official_source": True},
            }
        ]


class _EmptyDiscoveryProvider:
    """Return no new metadata so repair must rely on existing local filings."""

    def discover_filings(self, ticker: str, company_name: str, market: str, years: int = 5) -> list[dict[str, Any]]:
        return []


class _NoopDownloadProvider:
    """Downloader that should not be called when a local failed filing exists."""

    def download_filing(self, filing_meta: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("existing downloaded filings should be reparsed without downloading")


class _StaticParserService:
    """Parser returning enough text for chunk creation."""

    def parse(self, local_path: str, file_type: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "page_number": 1,
                "section": "Business",
                "text": "公司主营业务、商业模式、竞争格局和风险因素。" * 20,
            }
        ]


class _FallbackParserService:
    """Parser that forces txt failure and raw PDF/HTML success."""

    def parse(self, local_path: str, file_type: str | None = None) -> list[dict[str, Any]]:
        if file_type == "txt":
            return []
        if file_type in {"pdf", "html"}:
            return [
                {
                    "page_number": 1 if file_type == "pdf" else None,
                    "section": "Annual Report",
                    "text": "business model competition management compensation risk factors segments " * 20,
                }
            ]
        return []


class _StaticEmbeddingService:
    """Return deterministic small embeddings for tests."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _NoopVectorStore:
    """Vector store test double."""

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        return []


def test_filing_service_reparse_existing_downloaded_failed_filing(session, tmp_path) -> None:
    """A previously failed local PDF should be reparsed into chunks on the next run."""

    file_url = "https://static.cninfo.com.cn/finalpage/2026-04-30/example.PDF"
    local_pdf = tmp_path / "example.pdf"
    local_pdf.write_bytes(b"%PDF-1.4\n% test placeholder")
    now = datetime.now(timezone.utc)
    company = Company(ticker="SZ.002027", company_name="分众传媒", created_at=now, updated_at=now)
    session.add(company)
    session.flush()
    filing = Filing(
        company_id=company.id,
        ticker=company.ticker,
        company_name=company.company_name,
        market="CN",
        filing_type="annual_report",
        period_type="annual",
        fiscal_year=2025,
        fiscal_period="FY",
        title="2025 Annual Report",
        source_name="cninfo_query",
        source_url="https://www.cninfo.com.cn/new/fulltextSearch",
        file_url=file_url,
        local_path=str(local_pdf),
        file_type="pdf",
        checksum="abc",
        file_size=local_pdf.stat().st_size,
        mime_type="application/pdf",
        download_status="downloaded",
        parse_status="failed",
        metadata_json={"parse_error": "empty_or_failed"},
        created_at=now,
        updated_at=now,
    )
    session.add(filing)
    session.flush()
    session.commit()

    service = FilingService(
        discovery_provider=_StaticDiscoveryProvider(file_url),
        download_provider=_NoopDownloadProvider(),
        parser_service=_StaticParserService(),
        embedding_service=_StaticEmbeddingService(),
        vector_store=_NoopVectorStore(),
    )

    filings, warnings = service.ingest_company_filings(
        session,
        company,
        market="CN",
        years=1,
        filing_types={"annual_report"},
    )

    assert warnings == []
    assert filings[0].parse_status == "parsed"
    assert filings[0].metadata_json["reparsed_from_local"] is True
    assert filings[0].metadata_json["text_local_path"].endswith(".txt")
    assert len(get_filing_chunks_by_filing(session, filing.id)) >= 1


def test_filing_service_repairs_existing_failed_filings_before_discovery(session, tmp_path) -> None:
    """Existing failed filings should be repaired even when discovery finds nothing new."""

    file_url = "https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0409/2024040900356.pdf"
    local_pdf = tmp_path / "smic.pdf"
    local_pdf.write_bytes(b"%PDF-1.4\n% local placeholder")
    now = datetime.now(timezone.utc)
    company = Company(ticker="HK.00981", company_name="中芯国际", created_at=now, updated_at=now)
    session.add(company)
    session.flush()
    filing = Filing(
        company_id=company.id,
        ticker=company.ticker,
        company_name=company.company_name,
        market="HK",
        filing_type="annual_report",
        period_type="annual",
        fiscal_year=2023,
        fiscal_period="FY",
        title="2023 Annual Report",
        source_name="hkexnews_api",
        source_url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
        file_url=file_url,
        local_path=str(local_pdf),
        file_type="pdf",
        checksum="def",
        file_size=local_pdf.stat().st_size,
        mime_type="application/pdf",
        download_status="downloaded",
        parse_status="failed",
        metadata_json={"parse_error": "empty_or_failed"},
        created_at=now,
        updated_at=now,
    )
    session.add(filing)
    session.flush()
    session.commit()

    service = FilingService(
        discovery_provider=_EmptyDiscoveryProvider(),
        download_provider=_NoopDownloadProvider(),
        parser_service=_StaticParserService(),
        embedding_service=_StaticEmbeddingService(),
        vector_store=_NoopVectorStore(),
    )

    filings, warnings = service.ingest_company_filings(
        session,
        company,
        market="HK",
        years=1,
        filing_types={"annual_report"},
    )

    assert warnings == []
    assert filings[0].parse_status == "parsed"
    assert len(get_filing_chunks_by_filing(session, filing.id)) >= 1


def test_filing_service_demotes_annual_report_publication_notice(session, tmp_path) -> None:
    """Annual report notification letters should never count as usable annual reports."""

    file_url = "https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0409/notice.pdf"
    local_pdf = tmp_path / "notice.pdf"
    local_pdf.write_bytes(b"%PDF-1.4\n% local placeholder")
    now = datetime.now(timezone.utc)
    company = Company(ticker="HK.00981", company_name="中芯国际", created_at=now, updated_at=now)
    session.add(company)
    session.flush()
    filing = Filing(
        company_id=company.id,
        ticker=company.ticker,
        company_name=company.company_name,
        market="HK",
        filing_type="annual_report",
        period_type="annual",
        fiscal_year=2023,
        fiscal_period="FY",
        title="Notification Letter and Reply Form to Non-registered Holders of Securities - 2023 Annual Report",
        source_name="hkexnews_api",
        source_url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
        file_url=file_url,
        local_path=str(local_pdf),
        file_type="pdf",
        checksum="notice",
        file_size=local_pdf.stat().st_size,
        mime_type="application/pdf",
        download_status="downloaded",
        parse_status="failed",
        metadata_json={"parse_error": "empty_or_failed"},
        created_at=now,
        updated_at=now,
    )
    session.add(filing)
    session.flush()
    session.commit()

    service = FilingService(
        discovery_provider=_EmptyDiscoveryProvider(),
        download_provider=_NoopDownloadProvider(),
        parser_service=_StaticParserService(),
        embedding_service=_StaticEmbeddingService(),
        vector_store=_NoopVectorStore(),
    )

    service.ingest_company_filings(
        session,
        company,
        market="HK",
        years=1,
        filing_types={"annual_report"},
    )

    session.refresh(filing)
    assert filing.filing_type == "ir_material"
    assert filing.parse_status == "ignored"
    assert filing.metadata_json["ignored_reason"] == "annual_report_publication_notice"
    assert get_effective_filings(session, company.id) == []


def test_filing_service_rebuild_ignores_search_page_stub_rows(session, tmp_path) -> None:
    """Search-page placeholders must never be converted into vector chunks."""

    stored_txt = tmp_path / "search.txt"
    stored_txt.write_text("Search page text should not enter vector DB", encoding="utf-8")
    now = datetime.now(timezone.utc)
    company = Company(ticker="US.NVDA", company_name="NVIDIA", created_at=now, updated_at=now)
    session.add(company)
    session.flush()
    filing = Filing(
        company_id=company.id,
        ticker=company.ticker,
        company_name=company.company_name,
        market="US",
        filing_type="annual_report",
        period_type="annual",
        fiscal_year=2026,
        fiscal_period="FY",
        title="NVIDIA 10-K search candidate",
        source_name="sec_edgar_full_text_search",
        source_url="https://www.sec.gov/edgar/search/",
        file_url="https://www.sec.gov/edgar/search/?r=el#/q=10-K",
        local_path=str(stored_txt),
        file_type="txt",
        checksum="search",
        file_size=stored_txt.stat().st_size,
        mime_type="text/plain",
        download_status="downloaded",
        parse_status="parsed",
        metadata_json={"stub": True},
        created_at=now,
        updated_at=now,
    )
    session.add(filing)
    session.flush()
    session.commit()

    service = FilingService(
        discovery_provider=_EmptyDiscoveryProvider(),
        download_provider=_NoopDownloadProvider(),
        parser_service=_StaticParserService(),
        embedding_service=_StaticEmbeddingService(),
        vector_store=_NoopVectorStore(),
    )

    result = service.rebuild_local_filing_index(session, company, filing_types={"annual_report"})

    session.refresh(filing)
    assert result["processed"] == 0
    assert filing.parse_status == "ignored"
    assert filing.metadata_json["ignored_reason"] == "search_page_or_stub"
    assert get_filing_chunks_by_filing(session, filing.id) == []


@pytest.mark.parametrize(
    ("ticker", "company_name", "market", "raw_suffix", "raw_type"),
    [
        ("US.TSM", "TSMC", "US", ".html", "html"),
        ("HK.09618", "JD.com", "HK", ".pdf", "pdf"),
        ("SZ.002415", "海康威视", "CN", ".pdf", "pdf"),
    ],
)
def test_filing_service_repairs_empty_txt_by_falling_back_to_raw_artifact(
    session,
    tmp_path,
    ticker: str,
    company_name: str,
    market: str,
    raw_suffix: str,
    raw_type: str,
) -> None:
    """US/CN/HK filings should parse raw artifacts when stored txt is empty."""

    now = datetime.now(timezone.utc)
    company = Company(ticker=ticker, company_name=company_name, created_at=now, updated_at=now)
    session.add(company)
    session.flush()

    stored_txt = tmp_path / f"{ticker}_annual_report_2025_deadbeef.txt"
    raw_file = tmp_path / f"{ticker}_annual_report_2025.raw{raw_suffix}"
    stored_txt.write_text("", encoding="utf-8")
    raw_file.write_text("<html><body>Annual report business model</body></html>" if raw_type == "html" else "%PDF-1.4", encoding="utf-8")
    stored_txt.with_suffix(stored_txt.suffix + ".meta.json").write_text(
        json.dumps({"raw_local_path": str(raw_file), "raw_file_type": raw_type, "text_local_path": str(stored_txt)}),
        encoding="utf-8",
    )

    filing = Filing(
        company_id=company.id,
        ticker=company.ticker,
        company_name=company.company_name,
        market=market,
        filing_type="annual_report",
        period_type="annual",
        fiscal_year=2025,
        fiscal_period="FY",
        title="2025 Annual Report",
        source_name="official_test",
        source_url="https://example.test/search",
        file_url=f"https://example.test/{ticker}/annual-report",
        local_path=str(stored_txt),
        file_type="txt",
        checksum="fallback",
        file_size=0,
        mime_type="text/plain",
        download_status="downloaded",
        parse_status="failed",
        metadata_json={"parse_error": "empty_or_failed"},
        created_at=now,
        updated_at=now,
    )
    session.add(filing)
    session.flush()
    session.commit()

    service = FilingService(
        discovery_provider=_EmptyDiscoveryProvider(),
        download_provider=_NoopDownloadProvider(),
        parser_service=_FallbackParserService(),
        embedding_service=_StaticEmbeddingService(),
        vector_store=_NoopVectorStore(),
    )

    service.ingest_company_filings(session, company, market=market, years=1, filing_types={"annual_report"})

    session.refresh(filing)
    assert filing.parse_status == "parsed"
    assert filing.metadata_json["parsed_from_path"] == str(raw_file)
    assert filing.metadata_json["text_local_path"].endswith(".txt")
    assert len(get_filing_chunks_by_filing(session, filing.id)) >= 1
