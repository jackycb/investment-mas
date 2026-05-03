"""Workflow integration tests."""

from datetime import datetime, timezone

from app.db.models import CompanyDocument, DocumentChunk, Filing
from app.graph.state import create_initial_state
from app.graph.workflow import build_research_workflow
from tests.helpers import load_demo_dataset


def test_workflow_runs_end_to_end(session) -> None:
    load_demo_dataset(session)
    workflow = build_research_workflow(session)
    result = workflow.invoke(create_initial_state("NVDA", "NVIDIA"))
    assert result["company_profile"]
    assert result["peer_list"]
    assert len(result["financial_summary"]["years"]) >= 2
    assert result["valuation_result"]["blended"]["base"] > 0
    assert result["risk_assessment"]["overall_risk_score"] >= 0
    assert result["final_report"].startswith("# Investment Research Report")
    assert isinstance(result["errors"], list)
    assert isinstance(result.get("report_json", {}), dict)


def test_research_api_run_and_latest(client, session) -> None:
    load_demo_dataset(session)
    response = client.post("/research/run", json={"ticker": "NVDA", "company_name": "NVIDIA"})
    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "US.NVDA"
    assert body["final_rating"] in {"buy", "watch", "avoid", "research_more"}
    latest = client.get("/research/NVDA/latest")
    assert latest.status_code == 200
    assert latest.json()["report_json"]["ticker"] == "US.NVDA"


def test_workflow_survives_provider_failure(session) -> None:
    state = create_initial_state("NONEXIST", "Does Not Exist Corp")
    graph = build_research_workflow(session)
    result = graph.invoke(state)
    assert "errors" in result
    assert isinstance(result.get("final_report", ""), str)


def test_research_document_endpoints(client, session) -> None:
    load_demo_dataset(session)
    from app.db.repositories.company_repository import get_company_by_ticker

    company = get_company_by_ticker(session, "NVDA")
    assert company is not None
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
            section="Business Overview",
            page_number=1,
            chunk_index=1,
            text="NVIDIA develops accelerated computing platforms.",
            chunk_metadata={},
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    response = client.get("/research/NVDA/documents")
    assert response.status_code == 200
    body = response.json()
    assert body["documents"][0]["doc_type"] == "annual_report"
    assert body["documents"][0]["chunk_count"] == 1

    chunk_response = client.get(f"/research/NVDA/documents/{document.id}/chunks")
    assert chunk_response.status_code == 200
    chunk_body = chunk_response.json()
    assert chunk_body["chunks"][0]["section"] == "Business Overview"


def test_research_filing_endpoint_filters_placeholder_rows(client, session) -> None:
    load_demo_dataset(session)
    from app.db.repositories.company_repository import get_company_by_ticker

    company = get_company_by_ticker(session, "NVDA")
    assert company is not None
    session.add_all(
        [
            Filing(
                company_id=company.id,
                ticker="NVDA",
                company_name="NVIDIA",
                market="US",
                filing_type="annual_report",
                fiscal_year=2026,
                title="Old search page",
                source_name="sec_edgar_search",
                source_url="https://www.sec.gov",
                file_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=NVIDIA",
                local_path="/tmp/nvda-search.txt",
                file_type="html",
                checksum="old",
                download_status="downloaded",
                parse_status="pending",
                metadata_json={},
            ),
            Filing(
                company_id=company.id,
                ticker="NVDA",
                company_name="NVIDIA",
                market="US",
                filing_type="annual_report",
                fiscal_year=2026,
                title="NVIDIA 10-K",
                source_name="sec_edgar_direct_filing",
                source_url="https://www.sec.gov",
                file_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm",
                local_path="/tmp/nvda-10k.txt",
                file_type="html",
                checksum="real",
                download_status="downloaded",
                parse_status="parsed",
                metadata_json={"raw_local_path": "/tmp/nvda-10k.raw.html"},
            ),
        ]
    )
    session.commit()

    response = client.get("/research/NVDA/filings")
    assert response.status_code == 200
    body = response.json()
    assert len(body["filings"]) == 1
    assert body["filings"][0]["source_name"] == "sec_edgar_direct_filing"
    assert body["filings"][0]["raw_local_path"].endswith(".raw.html")
