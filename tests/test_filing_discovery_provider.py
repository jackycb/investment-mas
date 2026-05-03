"""Tests for market filing discovery provider adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, patch

from sqlalchemy import text

from app.db.models import Company
from app.providers.company_info_provider import get_canonical_company_name
from app.providers.filing_discovery_provider import HKEXNewsFilingDiscoveryProvider, MarketFilingDiscoveryProvider, USEdgarFilingDiscoveryProvider
from app.services.filing_service import FilingService


def test_market_filing_discovery_provider_returns_cninfo_and_sec_candidates() -> None:
    provider = MarketFilingDiscoveryProvider()

    cn_items = provider.discover_filings("SH.601857", "PetroChina", "CN", years=1)
    hk_items = provider.discover_filings("HK.00700", "Tencent", "HK", years=1)
    us_items = provider.discover_filings("PDD", "PDD Holdings", "US", years=1)

    assert cn_items
    assert cn_items[0]["source_name"] == "cninfo_direct_pdf"
    assert "static.cninfo.com.cn" in cn_items[0]["file_url"]
    assert hk_items
    assert hk_items[0]["source_name"] == "hkexnews_search"
    assert us_items
    assert us_items[0]["source_name"] in {"sec_edgar_direct_filing", "sec_edgar_submissions"}


def test_hk_filing_discovery_extracts_hkex_links() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()
    html_text = """
    <html><body>
      <a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0401/2024040100012.pdf">Tencent 2024 Annual Report</a>
      <a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0310/2024031000088.htm">2024 年度业绩公告</a>
      <a data-href="https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0415/2024041500044.pdf">2024 Interim Report</a>
      <a onclick="window.open('https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0501/2024050100023.pdf')">2024 Results Presentation</a>
      <a href="https://www1.hkexnews.hk/other/search">Search</a>
    </body></html>
    """

    items = provider._extract_hkex_candidates(  # type: ignore[attr-defined]
        html_text,
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en&stock=00700",
        "HK.00700",
        "Tencent",
        5,
    )

    assert len(items) >= 2
    assert any(item["file_type"] == "pdf" for item in items)
    assert all(item["source_name"] == "hkexnews" for item in items)
    assert any(item["filing_type"] == "annual_report" for item in items)
    assert any(item["filing_type"] == "interim_report" for item in items)
    assert any(item["filing_type"] == "earnings_presentation" for item in items)


def test_hkex_discovery_ignores_agm_and_voting_result_false_positives() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()
    html_text = """
    <html><body>
      <a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2025/1002/2025100203185.pdf">VOTING RESULTS OF 2025 ANNUAL GENERAL MEETING</a>
      <a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0911/2025091100013.pdf">NOTICE OF ANNUAL GENERAL MEETING</a>
      <a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0626/2025062601064.pdf">FISCAL YEAR 2025 ANNUAL REPORT</a>
    </body></html>
    """

    items = provider._extract_hkex_candidates(  # type: ignore[attr-defined]
        html_text,
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=1000015694",
        "HK.09988",
        "Alibaba",
        5,
    )

    assert len(items) == 1
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0626/2025062601064.pdf"


def test_hkex_result_row_extraction_prefers_pdf_from_search_results() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()
    html_text = """
    <html><body>
      <table>
        <tr>
          <td>09/04/2026 17:21</td>
          <td>00700<br/>80700</td>
          <td>TENCENT<br/>TENCENT-R</td>
          <td>
            Financial Statements/ESG Information - [Annual Report]
            <a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040900764.pdf">ANNUAL REPORT 2025</a>
          </td>
        </tr>
        <tr>
          <td>26/09/2025 06:05</td>
          <td>00700</td>
          <td>TENCENT</td>
          <td><a href="https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0926/2025092600999.pdf">VOTING RESULTS OF 2025 ANNUAL GENERAL MEETING</a></td>
        </tr>
      </table>
    </body></html>
    """

    items = provider._extract_hkex_result_rows(  # type: ignore[attr-defined]
        html_text,
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
        "HK.00700",
        "Tencent",
        5,
    )

    assert len(items) == 1
    assert items[0]["source_name"] == "hkexnews_search_result"
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040900764.pdf"


def test_hkex_search_url_prefers_stock_id_when_known() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    search_url = provider._hkex_search_url("HK.00700")  # type: ignore[attr-defined]
    candidate_urls = provider._candidate_search_urls("HK.00700")  # type: ignore[attr-defined]

    assert "stockId=7609" in search_url
    assert candidate_urls[0] == search_url
    assert any("stock=00700" in url for url in candidate_urls)


def test_hkex_search_url_supports_alibaba_stock_id() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    search_url = provider._hkex_search_url("HK.09988")  # type: ignore[attr-defined]

    assert "stockId=1000015694" in search_url


def test_hkex_search_url_supports_cnooc_stock_id() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    search_url = provider._hkex_search_url("HK.00883")  # type: ignore[attr-defined]

    assert "stockId=2088" in search_url


def test_hkex_search_url_supports_jd_stock_id() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    search_url = provider._hkex_search_url("HK.09618")  # type: ignore[attr-defined]

    assert "stockId=1000042149" in search_url


def test_hkex_search_url_supports_trip_com_stock_id() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    search_url = provider._hkex_search_url("HK.09961")  # type: ignore[attr-defined]

    assert "stockId=1000090312" in search_url


def test_hkex_discovery_prepends_alibaba_direct_annual_report() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    items = provider._prepend_direct_overrides(  # type: ignore[attr-defined]
        "HK.09988",
        "Alibaba",
        [{"file_url": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en&stock=09988", "source_name": "hkexnews_search"}],
    )

    assert items
    assert items[0]["source_name"] == "hkexnews_direct_pdf"
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0626/2025062601064.pdf"


def test_hkex_discovery_prepends_cnooc_direct_annual_report() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    items = provider._prepend_direct_overrides(  # type: ignore[attr-defined]
        "HK.00883",
        "CNOOC",
        [{"file_url": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en&stock=00883", "source_name": "hkexnews_search"}],
    )

    assert items
    assert items[0]["source_name"] == "hkexnews_direct_pdf"
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040900764.pdf"


def test_hkex_discovery_prepends_jd_direct_annual_report() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    items = provider._prepend_direct_overrides(  # type: ignore[attr-defined]
        "HK.09618",
        "JD.com",
        [{"file_url": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en&stock=09618", "source_name": "hkexnews_search"}],
    )

    assert items
    assert items[0]["source_name"] == "hkexnews_direct_pdf"
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0416/2026041601496.pdf"


def test_hkex_discovery_prepends_trip_com_direct_annual_report() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()

    items = provider._prepend_direct_overrides(  # type: ignore[attr-defined]
        "HK.09961",
        "Trip.com",
        [{"file_url": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en&stock=09961", "source_name": "hkexnews_search"}],
    )

    assert items
    assert items[0]["source_name"] == "hkexnews_direct_pdf"
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0428/2026042803041.pdf"


def test_hkex_stock_id_resolution_uses_official_prefix_endpoint() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()
    response = Mock()
    response.raise_for_status = Mock()
    response.text = 'callback({"more":"1","stockInfo":[{"stockId":1000090312,"code":"09961","name":"TRIP.COM-S"}]});'
    client = Mock()
    client.get.return_value = response
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        stock_id = provider._resolve_hkex_stock_id("HK.09961", "携程")  # type: ignore[attr-defined]

    assert stock_id == "1000090312"
    assert "prefix.do" in client.get.call_args.args[0]


def test_hkex_api_result_extraction_returns_latest_annual_report_pdf() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()
    payload = {
        "result": """[
            {
                "FILE_INFO": "2MB",
                "NEWS_ID": "12129798",
                "SHORT_TEXT": "Financial Statements&#x2f;ESG Information - [Annual Report]<br/>",
                "TOTAL_COUNT": "6",
                "STOCK_NAME": "TRIP.COM-S",
                "TITLE": "2025 Annual Report",
                "FILE_TYPE": "PDF",
                "DATE_TIME": "28/04/2026 18:40",
                "LONG_TEXT": "Financial Statements&#x2f;ESG Information - [Annual Report]",
                "STOCK_CODE": "09961",
                "FILE_LINK": "/listedco/listconews/sehk/2026/0428/2026042803041.pdf"
            },
            {
                "TITLE": "VOTING RESULTS OF 2025 ANNUAL GENERAL MEETING",
                "STOCK_CODE": "09961",
                "FILE_LINK": "/listedco/listconews/sehk/2026/0926/2026092600999.pdf"
            }
        ]"""
    }

    items = provider._extract_hkex_api_results(  # type: ignore[attr-defined]
        payload,
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=1000090312",
        "HK.09961",
        "Trip.com",
        5,
    )

    assert len(items) == 1
    assert items[0]["source_name"] == "hkexnews_api"
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0428/2026042803041.pdf"
    assert items[0]["fiscal_year"] == 2025


def test_hkex_api_result_extraction_accepts_dual_stock_code_rows() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()
    payload = {
        "result": """[
            {
                "TITLE": "ANNUAL REPORT 2025",
                "DATE_TIME": "09/04/2026 17:21",
                "LONG_TEXT": "Financial Statements&#x2f;ESG Information - [Annual Report]",
                "STOCK_CODE": "00700<br/>80700",
                "FILE_LINK": "/listedco/listconews/sehk/2026/0409/2026040901231.pdf"
            }
        ]"""
    }

    items = provider._extract_hkex_api_results(  # type: ignore[attr-defined]
        payload,
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=7609",
        "HK.00700",
        "Tencent",
        5,
    )

    assert len(items) == 1
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040901231.pdf"


def test_hkex_api_result_extraction_ignores_annual_report_notification_letters() -> None:
    provider = HKEXNewsFilingDiscoveryProvider()
    payload = {
        "result": """[
            {
                "TITLE": "Notification Letter and Reply Form to Non-registered Holders - Notice of Publication of 2023 Annual Report",
                "DATE_TIME": "09/04/2024 18:00",
                "LONG_TEXT": "Financial Statements&#x2f;ESG Information - [Annual Report]",
                "STOCK_CODE": "00981",
                "FILE_LINK": "/listedco/listconews/sehk/2024/0409/2024040900394.pdf"
            },
            {
                "TITLE": "2023 Annual Report",
                "DATE_TIME": "09/04/2024 18:00",
                "LONG_TEXT": "Financial Statements&#x2f;ESG Information - [Annual Report]",
                "STOCK_CODE": "00981",
                "FILE_LINK": "/listedco/listconews/sehk/2024/0409/2024040900356.pdf"
            }
        ]"""
    }

    items = provider._extract_hkex_api_results(  # type: ignore[attr-defined]
        payload,
        "https://www1.hkexnews.hk/search/titlesearch.xhtml?category=0&lang=EN&market=SEHK&stockId=7249",
        "HK.00981",
        "SMIC",
        5,
    )

    assert len(items) == 1
    assert items[0]["title"] == "2023 Annual Report"
    assert items[0]["file_url"] == "https://www1.hkexnews.hk/listedco/listconews/sehk/2024/0409/2024040900356.pdf"


def test_sec_discovery_prefers_full_text_search_for_pdd() -> None:
    provider = MarketFilingDiscoveryProvider()

    items = provider.discover_filings("PDD", "PDD Holdings", "US", years=1)

    assert items
    assert any(item["source_name"] == "sec_edgar_full_text_search" for item in items)
    search_item = next(item for item in items if item["source_name"] == "sec_edgar_full_text_search")
    assert "q=20-F" in search_item["source_url"]
    assert "ciks=0001737806" in search_item["source_url"]


def test_sec_discovery_prepends_direct_pdd_filing() -> None:
    provider = MarketFilingDiscoveryProvider()

    items = provider.discover_filings("US.PDD", "PDD", "US", years=1)

    assert items
    assert items[0]["source_name"] in {"sec_edgar_direct_filing", "sec_edgar_submissions"}
    assert "sec.gov/Archives/edgar/data/1737806/" in items[0]["file_url"]
    assert "x20f" in items[0]["file_url"].lower()


def test_sec_discovery_prepends_direct_nvda_filing() -> None:
    provider = USEdgarFilingDiscoveryProvider()
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0001045810-25-000023"],
                "filingDate": ["2025-02-26"],
                "reportDate": ["2025-01-26"],
                "primaryDocument": ["nvda-20250126.htm"],
            }
        }
    }
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = payload
    client = Mock()
    client.get.return_value = response
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        items = provider.discover_filings("US.NVDA", "NVIDIA", "US", years=1)

    assert items
    assert items[0]["source_name"] == "sec_edgar_submissions"
    assert items[0]["file_url"] == "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/nvda-20250126.htm"


def test_sec_discovery_prefers_submissions_api_when_available() -> None:
    provider = USEdgarFilingDiscoveryProvider()
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K", "8-K"],
                "accessionNumber": ["0001045810-26-000021", "0001045810-26-000099"],
                "filingDate": ["2026-02-25", "2026-03-01"],
                "reportDate": ["2026-01-25", "2026-02-28"],
                "primaryDocument": ["nvda-20260125.htm", "nvda-8k.htm"],
            }
        }
    }
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = payload
    client = Mock()
    client.get.return_value = response
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        items = provider.discover_filings("US.NVDA", "NVIDIA", "US", years=1)

    assert items
    assert items[0]["source_name"] == "sec_edgar_submissions"
    assert items[0]["file_url"] == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"


def test_sec_discovery_resolves_generic_ticker_via_official_ticker_map() -> None:
    provider = USEdgarFilingDiscoveryProvider()
    ticker_map_payload = {
        "0": {"ticker": "TSLA", "cik_str": 1318605, "title": "Tesla, Inc."},
    }
    submissions_payload = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0000950170-26-020778"],
                "filingDate": ["2026-01-29"],
                "reportDate": ["2025-12-31"],
                "primaryDocument": ["tsla-20251231.htm"],
            }
        }
    }

    ticker_map_response = Mock()
    ticker_map_response.raise_for_status = Mock()
    ticker_map_response.json.return_value = ticker_map_payload

    submissions_response = Mock()
    submissions_response.raise_for_status = Mock()
    submissions_response.json.return_value = submissions_payload

    client = Mock()
    client.get.side_effect = [ticker_map_response, submissions_response]
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        items = provider.discover_filings("US.TSLA", "Tesla", "US", years=1)

    assert items
    assert items[0]["source_name"] == "sec_edgar_submissions"
    assert items[0]["file_url"] == "https://www.sec.gov/Archives/edgar/data/1318605/000095017026020778/tsla-20251231.htm"
    assert "ciks=0001318605" in items[1]["source_url"]


def test_cninfo_rendered_result_extraction_prefers_annual_report_pdf() -> None:
    provider = MarketFilingDiscoveryProvider().cn_provider
    html_text = """
    <html><body>
      <div class="result">
        <span>2026-04-29</span>
        <a href="https://static.cninfo.com.cn/finalpage/2026-04-29/1234567890.PDF">迈瑞医疗：2025年年度报告</a>
      </div>
      <div class="result">
        <span>2026-04-29</span>
        <a href="https://static.cninfo.com.cn/finalpage/2026-04-29/1234567891.PDF">迈瑞医疗：2025年年度报告摘要</a>
      </div>
    </body></html>
    """

    items = provider._extract_cninfo_rendered_results(  # type: ignore[attr-defined]
        html_text,
        "https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord=%E8%BF%88%E7%91%9E%E5%8C%BB%E7%96%97&searchType=0",
        "SZ.300760",
        "迈瑞医疗",
        5,
    )

    assert len(items) == 1
    assert items[0]["source_name"] == "cninfo_search_result"
    assert items[0]["file_url"] == "https://static.cninfo.com.cn/finalpage/2026-04-29/1234567890.PDF"


def test_sec_discovery_handles_foreign_issuer_20f_via_official_ticker_map() -> None:
    provider = USEdgarFilingDiscoveryProvider()
    ticker_map_payload = {
        "0": {"ticker": "TSM", "cik_str": 1046179, "title": "Taiwan Semiconductor Manufacturing Co Ltd"},
    }
    submissions_payload = {
        "filings": {
            "recent": {
                "form": ["6-K", "20-F"],
                "accessionNumber": ["0001193125-26-100001", "0001193125-26-053248"],
                "filingDate": ["2026-03-12", "2026-04-10"],
                "reportDate": ["2026-03-11", "2025-12-31"],
                "primaryDocument": ["d6k.htm", "d20f.htm"],
            }
        }
    }

    ticker_map_response = Mock()
    ticker_map_response.raise_for_status = Mock()
    ticker_map_response.json.return_value = ticker_map_payload

    submissions_response = Mock()
    submissions_response.raise_for_status = Mock()
    submissions_response.json.return_value = submissions_payload

    client = Mock()
    client.get.side_effect = [ticker_map_response, submissions_response]
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        items = provider.discover_filings("US.TSM", "TSM", "US", years=1)

    assert items
    assert items[0]["source_name"] == "sec_edgar_submissions"
    assert items[0]["file_url"] == "https://www.sec.gov/Archives/edgar/data/1046179/000119312526053248/d20f.htm"
    assert items[0]["metadata_json"]["annual_form"] == "20-F"
    assert "q=20-F" in items[1]["source_url"]


def test_sec_discovery_prepends_direct_meta_filing() -> None:
    provider = USEdgarFilingDiscoveryProvider()
    payload = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "accessionNumber": ["0001326801-25-000017"],
                "filingDate": ["2025-01-30"],
                "reportDate": ["2024-12-31"],
                "primaryDocument": ["meta-20241231.htm"],
            }
        }
    }
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = payload
    client = Mock()
    client.get.return_value = response
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        items = provider.discover_filings("US.META", "Meta Platforms", "US", years=1)

    assert items
    assert items[0]["source_name"] == "sec_edgar_submissions"
    assert items[0]["file_url"] == "https://www.sec.gov/Archives/edgar/data/1326801/000132680125000017/meta-20241231.htm"


def test_cninfo_discovery_prefers_direct_moutai_filing() -> None:
    provider = MarketFilingDiscoveryProvider()

    items = provider.discover_filings("SH.600519", "贵州茅台", "CN", years=1)

    assert items
    assert items[0]["source_name"] == "cninfo_direct_pdf"
    assert items[0]["file_url"] == "https://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF"


def test_cninfo_discovery_prepends_direct_petrochina_filing() -> None:
    provider = MarketFilingDiscoveryProvider()

    items = provider.discover_filings("SH.601857", "中国石油", "CN", years=1)

    assert items
    assert items[0]["source_name"] == "cninfo_direct_pdf"
    assert items[0]["file_url"] == "https://static.cninfo.com.cn/finalpage/2025-03-31/1222962164.PDF"


def test_cninfo_query_discovery_resolves_annual_report_pdf() -> None:
    provider = MarketFilingDiscoveryProvider()
    top_search_payload = [
        {"code": "300760", "orgId": "9900035304", "zwjc": "迈瑞医疗", "type": "shj"},
    ]
    payload = {
        "announcements": [
            {
                "announcementId": "abc123",
                "announcementTitle": "迈瑞医疗2025年年度报告",
                "announcementTime": "2026-04-17",
                "adjunctUrl": "finalpage/2026-04-17/1225110000.PDF",
                "secCode": "300760",
            }
        ]
    }

    top_search_response = Mock()
    top_search_response.raise_for_status = Mock()
    top_search_response.json.return_value = top_search_payload

    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = payload
    client = Mock()
    client.post.side_effect = [top_search_response, response]
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        items = provider.discover_filings("SZ.300760", "迈瑞医疗", "CN", years=1)

    assert items
    assert items[0]["source_name"] == "cninfo_query"
    assert items[0]["file_url"] == "https://static.cninfo.com.cn/finalpage/2026-04-17/1225110000.PDF"
    query_payload = client.post.call_args_list[1].kwargs["data"]
    assert query_payload["stock"] == "300760,9900035304"


def test_cninfo_fulltext_api_discovery_resolves_annual_report_pdf() -> None:
    provider = MarketFilingDiscoveryProvider().cn_provider
    payload = {
        "announcements": [
            {
                "announcementTitle": "<em>迈瑞医疗</em>：2025年<em>年度报告</em>",
                "announcementTime": 1774886400000,
                "adjunctUrl": "finalpage/2026-03-31/1225059012.PDF",
                "secCode": "300760",
                "orgId": "9900035304",
                "announcementId": "1225059012",
            },
            {
                "announcementTitle": "<em>迈瑞医疗</em>：2025年<em>年度报告</em>摘要",
                "announcementTime": 1774886400000,
                "adjunctUrl": "finalpage/2026-03-31/1225059011.PDF",
                "secCode": "300760",
            },
        ]
    }

    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = payload
    client = Mock()
    client.get.return_value = response
    client_cm = Mock()
    client_cm.__enter__ = Mock(return_value=client)
    client_cm.__exit__ = Mock(return_value=False)

    with patch("app.providers.filing_discovery_provider.httpx.Client", return_value=client_cm):
        items = provider._discover_from_fulltext_api("SZ.300760", "迈瑞医疗", years=1)  # type: ignore[attr-defined]

    assert len(items) == 1
    assert items[0]["source_name"] == "cninfo_fulltext_api"
    assert items[0]["title"] == "迈瑞医疗：2025年年度报告"
    assert items[0]["file_url"] == "https://static.cninfo.com.cn/finalpage/2026-03-31/1225059012.PDF"


def test_sec_search_fallback_is_stub_not_download_candidate() -> None:
    provider = USEdgarFilingDiscoveryProvider()

    with patch.object(provider, "_resolve_cik", return_value=None):
        items = provider.discover_filings("US.UNKNOWN", "Unknown Co", "US", years=1)

    assert items
    assert items[0]["metadata_json"]["stub"] is True


def test_canonical_company_name_corrects_nvda_typo() -> None:
    assert get_canonical_company_name("US.NVDA", "NVIDA") == "NVIDIA"


def test_canonical_company_name_corrects_meta_alias() -> None:
    assert get_canonical_company_name("US.META", "Facebook") == "Meta Platforms"


def test_filing_service_gracefully_skips_when_tables_missing(session) -> None:
    company = Company(ticker="PDD", company_name="PDD Holdings", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    session.add(company)
    session.flush()
    session.execute(text("DROP TABLE filing_chunks"))
    session.execute(text("DROP TABLE filings"))
    session.commit()

    filings, warnings = FilingService().ingest_company_filings(session, company, market="US", years=1)

    assert filings == []
    assert warnings
    assert "alembic upgrade head" in warnings[0]
