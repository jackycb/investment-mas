"""Tests for CompanyProfileService."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Company, CompanyProfile, Filing, PeerMapping
from app.db.repositories.company_repository import get_company_by_ticker, get_latest_company_profile, get_or_create_company, get_peer_mappings
from app.providers.company_info_provider import MockCompanyInfoProvider
from app.services.company_profile_service import CompanyProfileService


def test_build_profile_returns_profile_and_peers(session) -> None:
    profile, peers, company = CompanyProfileService(provider=MockCompanyInfoProvider()).build_profile(session, "NVDA", "NVIDIA")
    assert isinstance(profile, dict)
    assert isinstance(peers, list)
    assert "business_model" in profile
    assert company is not None


def test_get_or_create_company_recovers_from_duplicate_ticker_failed_flush(session) -> None:
    now = datetime.now(timezone.utc)
    session.add(Company(ticker="HK.09961", company_name="Trip.com", created_at=now, updated_at=now))
    session.commit()

    session.add(Company(ticker="HK.09961", company_name="携程", created_at=now, updated_at=now))
    with pytest.raises(IntegrityError):
        session.flush()

    company = get_or_create_company(session, "HK.09961", "携程")

    assert company.id is not None
    assert company.ticker == "HK.09961"
    assert company.company_name == "携程"
    assert session.is_active


def test_unknown_ticker_uses_mock_fallback(session) -> None:
    profile, peers, company = CompanyProfileService(provider=MockCompanyInfoProvider()).build_profile(
        session, "UNKNOWN", "Unknown Corp"
    )
    assert isinstance(profile, dict)
    assert isinstance(peers, list)
    assert company is not None


def test_build_profile_persists_profile_and_peers(session) -> None:
    service = CompanyProfileService(provider=MockCompanyInfoProvider())
    profile, peers, company = service.build_profile(session, "HK.00700", "Tencent")
    assert company is not None
    stored_company = get_company_by_ticker(session, "HK.00700")
    assert stored_company is not None
    assert get_latest_company_profile(session, stored_company.id) is not None
    assert len(get_peer_mappings(session, stored_company.id)) == len(peers)
    assert profile["business_model"]


class _FakeRAGService:
    def __init__(self) -> None:
        self.called = False

    def build(self, session, company):
        self.called = True
        return (
            {
                "as_of_date": date.today().isoformat(),
                "business_model": "New profile from filing pipeline",
                "market_space": "",
                "competition_landscape": "",
                "corporate_culture": "",
                "revenue_model": "",
                "key_products": [],
                "key_regions": [],
                "key_customers": [],
                "key_suppliers": [],
                "risk_factors": [],
                "management_summary": "",
                "raw_sources": [{"source": "filing"}],
                "warnings": [],
            },
            {},
            [],
        )


class _FakePeerService:
    def discover(self, ticker, company_name, company_profile, evidence_bundle):
        return (
            [
                {
                    "ticker": "BIDU",
                    "company_name": "Baidu",
                    "peer_type": "core",
                    "similarity_score": 0.8,
                    "rationale": "Test peer",
                }
            ],
            [],
        )


def test_stub_filing_is_not_treated_as_usable_cached_source(session) -> None:
    now = datetime.now(timezone.utc)
    service = CompanyProfileService(rag_service=_FakeRAGService(), peer_service=_FakePeerService())

    company = service._get_or_create_company(session, "HK.00700", "Tencent")
    session.add(
        CompanyProfile(
            company_id=company.id,
            as_of_date=date.today(),
            business_model="Old profile",
            market_space="",
            competition_landscape="",
            corporate_culture="",
            revenue_model="",
            key_products=[],
            key_regions=[],
            key_customers=[],
            key_suppliers=[],
            risk_factors=[],
            management_summary="",
            raw_sources=[{"source": "old"}],
            created_at=now,
        )
    )

    peer_company = service._get_or_create_company(session, "BIDU", "Baidu")
    session.add(
        PeerMapping(
            company_id=company.id,
            peer_company_id=peer_company.id,
            peer_type="core",
            similarity_score=0.4,
            rationale="Old peer",
            source_type="provider",
            created_at=now,
        )
    )
    session.add(
        Filing(
            company_id=company.id,
            ticker=company.ticker,
            company_name=company.company_name,
            market="HK",
            filing_type="annual_report",
            period_type="annual",
            fiscal_year=2026,
            fiscal_period="FY",
            title="Old stub filing",
            source_name="hk_adapter_stub",
            source_url="https://example.com/stub",
            file_url="https://example.com/stub",
            local_path="/tmp/stub.html",
            file_type="html",
            download_status="downloaded",
            parse_status="pending",
            metadata_json={"stub": True},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()

    filings = [row for row in session.query(Filing).filter(Filing.company_id == company.id).all()]

    assert service._contains_only_stub_filings(filings) is True
    assert service._has_usable_filings(filings) is False


def test_search_page_html_is_not_treated_as_usable_filing(session) -> None:
    now = datetime.now(timezone.utc)
    service = CompanyProfileService(rag_service=_FakeRAGService(), peer_service=_FakePeerService())
    company = service._get_or_create_company(session, "HK.09988", "Alibaba")
    session.add(
        Filing(
            company_id=company.id,
            ticker=company.ticker,
            company_name=company.company_name,
            market="HK",
            filing_type="annual_report",
            period_type="annual",
            fiscal_year=2025,
            fiscal_period="FY",
            title="Alibaba HKEX search page",
            source_name="hkexnews_search",
            source_url="https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=1000015694",
            file_url="https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=1000015694",
            local_path="/tmp/alibaba-search.html",
            file_type="html",
            download_status="downloaded",
            parse_status="pending",
            metadata_json={"official_source": True},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()

    filings = [row for row in session.query(Filing).filter(Filing.company_id == company.id).all()]

    assert service._has_usable_filings(filings) is False


def test_search_page_txt_is_not_treated_as_usable_filing(session) -> None:
    now = datetime.now(timezone.utc)
    service = CompanyProfileService(rag_service=_FakeRAGService(), peer_service=_FakePeerService())
    company = service._get_or_create_company(session, "US.META", "Meta Platforms")
    session.add(
        Filing(
            company_id=company.id,
            ticker=company.ticker,
            company_name=company.company_name,
            market="US",
            filing_type="annual_report",
            period_type="annual",
            fiscal_year=2026,
            fiscal_period="FY",
            title="Meta SEC full-text search page",
            source_name="sec_edgar_full_text_search",
            source_url="https://www.sec.gov/edgar/search/?r=el#/q=10-K&dateRange=1y&ciks=0001326801&entityName=Meta+Platforms",
            file_url="https://www.sec.gov/edgar/search/?r=el#/q=10-K&dateRange=1y&ciks=0001326801&entityName=Meta+Platforms",
            local_path="/tmp/meta-search.txt",
            file_type="txt",
            download_status="downloaded",
            parse_status="parsed",
            metadata_json={"official_source": True},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()

    filings = [row for row in session.query(Filing).filter(Filing.company_id == company.id).all()]

    assert service._has_usable_filings(filings) is False


def test_browse_edgar_html_is_not_treated_as_usable_filing(session) -> None:
    now = datetime.now(timezone.utc)
    service = CompanyProfileService(rag_service=_FakeRAGService(), peer_service=_FakePeerService())
    company = service._get_or_create_company(session, "US.PDD", "PDD")
    session.add(
        Filing(
            company_id=company.id,
            ticker=company.ticker,
            company_name=company.company_name,
            market="US",
            filing_type="annual_report",
            period_type="annual",
            fiscal_year=2026,
            fiscal_period="FY",
            title="PDD browse edgar search page",
            source_name="sec_edgar_search",
            source_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=PDD",
            file_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=PDD",
            local_path="/tmp/pdd-search.html",
            file_type="html",
            download_status="downloaded",
            parse_status="pending",
            metadata_json={"official_source": True},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()

    filings = [row for row in session.query(Filing).filter(Filing.company_id == company.id).all()]

    assert service._has_usable_filings(filings) is False


def test_downloaded_but_unparsed_filing_is_not_treated_as_usable(session) -> None:
    now = datetime.now(timezone.utc)
    service = CompanyProfileService(rag_service=_FakeRAGService(), peer_service=_FakePeerService())
    company = service._get_or_create_company(session, "US.META", "Meta Platforms")
    session.add(
        Filing(
            company_id=company.id,
            ticker=company.ticker,
            company_name=company.company_name,
            market="US",
            filing_type="annual_report",
            period_type="annual",
            fiscal_year=2025,
            fiscal_period="FY",
            title="Meta 10-K half baked",
            source_name="sec_edgar_direct_filing",
            source_url="https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/0001628280-26-003942-index.htm",
            file_url="https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm",
            local_path="/tmp/meta-10k.txt",
            file_type="txt",
            download_status="downloaded",
            parse_status="pending",
            metadata_json={"raw_local_path": "/tmp/meta-10k.raw.html"},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()

    filings = [row for row in session.query(Filing).filter(Filing.company_id == company.id).all()]

    assert service._has_usable_filings(filings) is False
