"""Tests for Chinese annual-report coverage fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.models import Company, Filing, FilingChunk
from app.services.filing_coverage_service import FilingCoverageService


class _EmptyRetrieval:
    """Force SQL keyword fallback by returning no vector results."""

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        return []


def test_filing_coverage_service_detects_chinese_annual_report_topics(session) -> None:
    """Chinese filings should not be marked as zero coverage when chunks exist."""

    now = datetime.now(timezone.utc)
    company = Company(ticker="SZ.002415", company_name="海康威视", created_at=now, updated_at=now)
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
        title="2025年年度报告",
        source_name="cninfo_query",
        source_url="https://www.cninfo.com.cn/new/fulltextSearch",
        file_url="https://static.cninfo.com.cn/finalpage/2026-04-30/example.PDF",
        local_path="/tmp/example.txt",
        file_type="txt",
        checksum="example",
        file_size=100,
        mime_type="text/plain",
        download_status="downloaded",
        parse_status="parsed",
        metadata_json={"chunk_count": 6},
        created_at=now,
        updated_at=now,
    )
    session.add(filing)
    session.flush()

    texts = [
        "公司主营业务为智能物联产品和服务，经营模式覆盖研发、生产、销售和解决方案。",
        "公司面临行业竞争，竞争格局、市场份额和同行业公司变化可能影响经营。",
        "董事会和高级管理人员负责公司治理，管理层持续完善治理结构。",
        "董事、监事、高级管理人员报酬以及股权激励情况如下。",
        "重大风险因素和风险提示包括监管风险、诉讼风险和市场不确定性。",
        "主营业务分行业、分产品、分地区披露营业收入构成和分部信息。",
    ]
    for idx, text in enumerate(texts):
        session.add(
            FilingChunk(
                filing_id=filing.id,
                company_id=company.id,
                chunk_index=idx,
                section="年度报告",
                page_number=idx + 1,
                text=text,
                token_count=len(text),
                embedding_model="test",
                embedding_status="indexed",
                metadata_json={"filing_type": "annual_report"},
                created_at=now,
            )
        )
    session.flush()

    result = FilingCoverageService(retrieval_service=_EmptyRetrieval()).check(
        session,
        ticker="SZ.002415",
        company_profile={},
    )

    assert result["overall_coverage"] == 1.0
    assert result["missing"] == []


def test_filing_coverage_service_ignores_annual_report_notification_letter_chunks(session) -> None:
    """Old HKEX notification-letter chunks should not count as annual-report evidence."""

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
        title="Notification Letter and Reply Form to Non-registered Holders - Notice of Publication of 2023 Annual Report",
        source_name="hkexnews_api",
        source_url="https://www1.hkexnews.hk/search/titlesearch.xhtml",
        file_url="https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0409/notice.pdf",
        local_path="/tmp/notice.txt",
        file_type="txt",
        checksum="notice",
        file_size=100,
        mime_type="text/plain",
        download_status="downloaded",
        parse_status="parsed",
        metadata_json={"chunk_count": 1},
        created_at=now,
        updated_at=now,
    )
    session.add(filing)
    session.flush()
    session.add(
        FilingChunk(
            filing_id=filing.id,
            company_id=company.id,
            chunk_index=0,
            section="Notice",
            page_number=1,
            text="business model competition management compensation risk factors segments",
            token_count=64,
            embedding_model="test",
            embedding_status="indexed",
            metadata_json={"filing_type": "annual_report"},
            created_at=now,
        )
    )
    session.flush()

    result = FilingCoverageService(retrieval_service=_EmptyRetrieval()).check(
        session,
        ticker="HK.00981",
        company_profile={},
    )

    assert result["overall_coverage"] == 0.0
    assert set(result["missing"]) == {"business_model", "competition", "management", "compensation", "risk_factors", "segments"}
