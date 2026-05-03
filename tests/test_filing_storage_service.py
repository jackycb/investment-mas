"""Tests for filing storage and downloader plumbing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from app.providers.filing_download_provider import HttpFilingDownloadProvider
from app.services.document_storage_service import DocumentStorageService


def test_document_storage_service_persists_file_and_sidecar(tmp_path: Path) -> None:
    service = DocumentStorageService(base_dir=str(tmp_path))

    result = service.store_download(
        filing_meta={
            "ticker": "PDD",
            "company_name": "PDD Holdings",
            "market": "US",
            "filing_type": "annual_report",
            "fiscal_year": 2024,
            "file_url": "https://example.com/pdd-20f.html",
        },
        content=b"<html><body>PDD annual filing</body></html>",
        mime_type="text/html",
    )

    local_path = Path(result["local_path"])
    sidecar = Path(str(local_path) + ".meta.json")
    assert local_path.exists()
    assert local_path.suffix == ".txt"
    assert result["file_type"] == "txt"
    assert result["raw_local_path"] is not None
    assert Path(result["raw_local_path"]).suffix == ".html"
    assert Path(result["raw_local_path"]).exists()
    assert result["text_local_path"] == result["local_path"]
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["ticker"] == "PDD"
    assert payload["checksum"] == result["checksum"]
    assert payload["stored_suffix"] == ".txt"
    assert payload["raw_local_path"].endswith(".raw.html")
    assert payload["text_local_path"].endswith(".txt")


def test_document_storage_service_handles_html_with_encoding_declaration(tmp_path: Path) -> None:
    service = DocumentStorageService(base_dir=str(tmp_path))

    result = service.store_download(
        filing_meta={
            "ticker": "US.PDD",
            "company_name": "PDD",
            "market": "US",
            "filing_type": "annual_report",
            "fiscal_year": 2024,
            "file_url": "https://www.sec.gov/Archives/edgar/data/1737806/000141057825000951/pdd-20241231x20f.htm",
        },
        content=b'<?xml version="1.0" encoding="UTF-8"?><html><body><h1>20-F</h1><p>PDD annual report text.</p></body></html>',
        mime_type="text/html",
    )

    local_path = Path(result["local_path"])
    assert local_path.exists()
    assert local_path.suffix == ".txt"
    assert "PDD annual report text." in local_path.read_text(encoding="utf-8", errors="ignore")
    assert result["raw_local_path"] is not None
    assert Path(result["raw_local_path"]).exists()


def test_http_filing_download_provider_uses_sec_headers_and_persists_html(tmp_path: Path) -> None:
    provider = HttpFilingDownloadProvider(storage_service=DocumentStorageService(base_dir=str(tmp_path)))
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm"),
        headers={"content-type": "text/html; charset=utf-8"},
        content=b"<html><body><h1>Meta 10-K</h1><p>Annual report body.</p></body></html>",
    )
    mock_client = Mock()
    mock_client.get.return_value = response
    mock_cm = Mock()
    mock_cm.__enter__ = Mock(return_value=mock_client)
    mock_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_download_provider.httpx.Client", return_value=mock_cm):
        result = provider.download_filing(
            {
                "ticker": "US.META",
                "company_name": "Meta Platforms",
                "market": "US",
                "filing_type": "annual_report",
                "fiscal_year": 2025,
                "source_name": "sec_edgar_direct_filing",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/0001628280-26-003942-index.htm",
                "file_url": "https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/meta-20251231.htm",
                "file_type": "html",
            }
        )

    assert result["status"] == "downloaded"
    assert Path(result["local_path"]).suffix == ".txt"
    assert Path(result["raw_local_path"]).suffix == ".html"
    _, kwargs = mock_client.get.call_args
    assert kwargs["headers"]["Referer"].startswith("https://www.sec.gov/Archives/edgar/data/1326801/")
    assert "text/html" in kwargs["headers"]["Accept"]


def test_document_storage_service_converts_pdf_to_txt_and_keeps_raw_pdf(tmp_path: Path) -> None:
    service = DocumentStorageService(base_dir=str(tmp_path))

    with patch("app.services.document_storage_service.parse_pdf", return_value=[{"page_number": 1, "section": "Cover", "text": "Annual report body"}]):
        result = service.store_download(
            filing_meta={
                "ticker": "US.NVDA",
                "company_name": "NVIDIA",
                "market": "US",
                "filing_type": "annual_report",
                "fiscal_year": 2026,
                "file_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.pdf",
                "source_name": "sec_edgar_direct_filing",
            },
            content=b"%PDF-1.4 fake pdf bytes",
            mime_type="application/pdf",
        )

    assert Path(result["local_path"]).suffix == ".txt"
    assert Path(result["raw_local_path"]).suffix == ".pdf"
    assert "Annual report body" in Path(result["local_path"]).read_text(encoding="utf-8")
    pdf_files = list(Path(result["raw_local_path"]).parent.glob("*.pdf"))
    assert pdf_files == [Path(result["raw_local_path"])]
    assert Path(result["local_path"]).stem == Path(result["raw_local_path"]).stem


def test_document_storage_service_avoids_name_collision_between_search_and_direct_filing(tmp_path: Path) -> None:
    service = DocumentStorageService(base_dir=str(tmp_path))

    search_result = service.store_download(
        filing_meta={
            "ticker": "US.NVDA",
            "company_name": "NVIDIA",
            "market": "US",
            "filing_type": "annual_report",
            "fiscal_year": 2026,
            "file_url": "https://www.sec.gov/edgar/search/?q=annual+report",
            "source_name": "sec_edgar_full_text_search",
        },
        content=b"<html><body>search page</body></html>",
        mime_type="text/html",
    )
    direct_result = service.store_download(
        filing_meta={
            "ticker": "US.NVDA",
            "company_name": "NVIDIA",
            "market": "US",
            "filing_type": "annual_report",
            "fiscal_year": 2026,
            "file_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm",
            "source_name": "sec_edgar_direct_filing",
        },
        content=b"<html><body>actual 10-k</body></html>",
        mime_type="text/html",
    )

    assert search_result["local_path"] != direct_result["local_path"]
    assert search_result["raw_local_path"] != direct_result["raw_local_path"]
