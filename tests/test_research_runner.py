"""Tests for research runner persistence and streaming."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import CompanyDocument, DocumentChunk, Filing, NewsArticle, NewsEvent
from app.providers.news_provider import MockNewsProvider
from app.services.news_service import NewsService
from app.services.research_runner import ResearchRunner


def test_research_runner_persists_news_and_events(session) -> None:
    runner = ResearchRunner(session)
    news_service = NewsService(provider=MockNewsProvider())
    company_news, peer_news = news_service.collect_news(
        session=None,
        ticker="NVDA",
        company_name="NVIDIA",
        peers=[{"ticker": "AMD", "company_name": "AMD"}],
    )
    state = {
        "ticker": "NVDA",
        "company_name": "NVIDIA",
        "peer_list": [{"ticker": "AMD", "company_name": "AMD"}],
        "company_news": company_news,
        "peer_news": peer_news,
        "event_list": [
            {
                "ticker": "NVDA",
                "event_type": "margin_pressure",
                "event_summary": company_news[1]["summary"],
                "event_date": company_news[1]["published_at"],
                "sentiment": "negative",
                "importance_score": 0.8,
                "confidence_score": 0.7,
                "risk_tags": ["profitability"],
                "entities": ["NVDA"],
                "impact_dimensions": {"fundamentals": 0.3},
            }
        ],
    }
    runner._persist_news_and_events(state)
    assert session.query(NewsArticle).count() >= 3
    assert session.query(NewsEvent).count() == 1


def test_research_stream_endpoint_emits_progress_and_result(client, session) -> None:
    with client.stream("GET", "/research/stream", params={"ticker": "NVDA", "company_name": "NVIDIA"}) as response:
        body = "".join(chunk for chunk in response.iter_text())
    assert response.status_code == 200
    assert "event: started" in body
    assert "event: progress" in body
    assert "event: result" in body or "event: error" in body
    assert '"ticker": "US.NVDA"' in body


def test_research_runner_truncates_oversized_news_url(session) -> None:
    runner = ResearchRunner(session)
    company = runner._get_or_create_company("HK.00386", "Sinopec")
    article = {
        "title": "Sinopec Net Margin Compression Reinforces Bearish Earnings Narratives - simplywall.st",
        "source_name": "simplywall.st",
        "source_type": "finance_news",
        "url": "https://news.google.com/rss/articles/" + "A" * 1200,
        "published_at": "2026-03-22T07:00:00+00:00",
        "summary": "Sinopec Net Margin Compression Reinforces Bearish Earnings Narratives",
        "content": "Sinopec Net Margin Compression Reinforces Bearish Earnings Narratives",
        "language": "en",
    }
    stored = runner._upsert_article(company, article)
    assert stored.id is not None
    assert stored.url is not None
    assert len(stored.url) <= 500


def test_research_runner_company_profiler_details_include_document_context(session) -> None:
    runner = ResearchRunner(session)
    company = runner._get_or_create_company("NVDA", "NVIDIA")
    document = CompanyDocument(
        company_id=company.id,
        ticker="NVDA",
        company_name="NVIDIA",
        doc_type="annual_report",
        fiscal_year=2024,
        report_date=None,
        source_name="company_ir",
        source_url="https://example.com/nvda/ar.pdf",
        local_path="/tmp/nvda_ar.pdf",
        checksum="abc",
        downloaded_at=datetime.now(timezone.utc),
        raw_metadata={},
    )
    session.add(document)
    session.flush()
    session.add(
        DocumentChunk(
            company_document_id=document.id,
            company_id=company.id,
            ticker="NVDA",
            company_name="NVIDIA",
            doc_type="annual_report",
            fiscal_year=2024,
            section="Competition",
            page_number=12,
            chunk_index=1,
            text="Competition includes AMD and Broadcom.",
            chunk_metadata={},
            created_at=datetime.now(timezone.utc),
        )
    )
    session.flush()

    state = {
        "ticker": "NVDA",
        "company_name": "NVIDIA",
        "company_profile": {
            "raw_sources": [
                {"source": "company_document", "doc_type": "annual_report", "fiscal_year": 2024, "source_name": "company_ir"},
                {"source": "document_rag", "doc_type": "annual_report", "fiscal_year": 2024, "topics": ["competition", "business_model"]},
            ],
            "warnings": ["Parsed document evidence."],
        },
        "peer_list": [{"ticker": "AMD", "peer_type": "core", "rationale": "Directly mentioned in competition section of filings."}],
    }

    details = runner._build_step_details("company_profiler", type("Node", (), {"service": type("Svc", (), {"provider": None})()})(), state)  # type: ignore[arg-type]

    assert details["document_count"] == 1
    assert details["filing_status"] == "no_results"
    assert "competition" in details["evidence_topics"]
    assert details["peer_samples"][0]["ticker"] == "AMD"


def test_research_runner_company_profiler_details_mark_filing_degraded(session) -> None:
    runner = ResearchRunner(session)
    company = runner._get_or_create_company("PDD", "PDD Holdings")

    state = {
        "ticker": "PDD",
        "company_name": "PDD Holdings",
        "company_profile": {
            "raw_sources": [],
            "warnings": ["Skipped filing ingestion: filings / filing_chunks tables not created yet. Please run alembic upgrade head."],
        },
        "peer_list": [],
    }

    details = runner._build_step_details("company_profiler", type("Node", (), {"service": type("Svc", (), {"provider": None})()})(), state)  # type: ignore[arg-type]

    assert company.id is not None
    assert details["filing_status"] == "degraded"


def test_research_runner_company_profiler_details_filter_effective_filings(session) -> None:
    runner = ResearchRunner(session)
    company = runner._get_or_create_company("US.META", "Meta Platforms")
    session.add_all(
        [
            Filing(
                company_id=company.id,
                ticker="US.META",
                company_name="Meta Platforms",
                market="US",
                filing_type="annual_report",
                fiscal_year=2025,
                title="Browse Edgar Search",
                source_name="sec_edgar_search",
                source_url="https://www.sec.gov",
                file_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=Meta",
                local_path="/tmp/meta-search.txt",
                file_type="html",
                checksum="search",
                download_status="downloaded",
                parse_status="pending",
                metadata_json={},
            ),
            Filing(
                company_id=company.id,
                ticker="US.META",
                company_name="Meta Platforms",
                market="US",
                filing_type="annual_report",
                fiscal_year=2025,
                title="Meta 10-K",
                source_name="sec_edgar_direct_filing",
                source_url="https://www.sec.gov",
                file_url="https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm",
                local_path="/tmp/meta-10k.txt",
                file_type="html",
                checksum="real",
                download_status="downloaded",
                parse_status="parsed",
                metadata_json={"raw_local_path": "/tmp/meta-10k.raw.html"},
            ),
        ]
    )
    session.commit()

    state = {
        "ticker": "US.META",
        "company_name": "Meta Platforms",
        "company_profile": {"raw_sources": [], "warnings": []},
        "peer_list": [],
    }

    details = runner._build_step_details("company_profiler", type("Node", (), {"service": type("Svc", (), {"provider": None})()})(), state)  # type: ignore[arg-type]

    assert details["filing_count"] == 1
    assert details["filing_total_count"] == 2
    assert details["effective_filing_sources"][0]["source_name"] == "sec_edgar_direct_filing"
