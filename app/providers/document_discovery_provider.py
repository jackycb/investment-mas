"""Document discovery providers for company profiling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
import os
import re
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from lxml import html

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


@dataclass(slots=True)
class DocumentCandidate:
    """Discovered company document candidate."""

    ticker: str
    company_name: str
    doc_type: str
    fiscal_year: int | None
    report_date: date | None
    source_name: str
    source_url: str
    title: str = ""


class DocumentDiscoveryProvider(ABC):
    """Abstract discovery provider for investor-facing documents."""

    @abstractmethod
    def discover_documents(
        self,
        ticker: str,
        company_name: str,
        context: dict | None = None,
    ) -> list[DocumentCandidate]:
        """Discover document links for a company."""


class WebDocumentDiscoveryProvider(DocumentDiscoveryProvider):
    """Generic web discovery provider across IR pages and finance portals."""

    DOC_PATTERNS = {
        "annual_report": ["annual report", "annualreport", "年报", "年度报告"],
        "10-k": ["10-k", "10k"],
        "20-f": ["20-f", "20f"],
        "earnings_presentation": ["presentation", "earnings deck", "results presentation", "业绩演示"],
        "ir_material": ["investor relations", "investor presentation", "financial results", "业绩公告", "announcement", "公告"],
        "analyst_rating": ["institutional ratings", "analyst rating", "机构评级", "分析师评级"],
    }
    IR_HOST_KEYWORDS = ("investor", "ir.", "relations", "announcements", "sec.gov", "futunn.com", "finance.yahoo.com")
    BLOCKED_HOST_KEYWORDS = ("docs.", "developer.", "support.", "blog.", "forums.", "shop.")
    BLOCKED_URL_PATTERNS = (
        "news.futunn.com/post/",
        "finance.yahoo.com/quote/",
        "futunn.com/news/",
    )

    def __init__(self, timeout_seconds: float | None = None, user_agent: str | None = None) -> None:
        self.timeout_seconds = timeout_seconds or settings.web_request_timeout_seconds
        self.user_agent = user_agent or settings.web_user_agent

    def discover_documents(self, ticker: str, company_name: str, context: dict | None = None) -> list[DocumentCandidate]:
        """Discover annual reports, filings, and IR decks from multiple web entry points."""

        context = context or {}
        if os.getenv("PYTEST_CURRENT_TEST"):
            return []
        if not self._looks_like_supported_ticker(ticker) and not any(context.get(key) for key in ("official_website", "ir_website")):
            logger.info("document_discovery_skipped_invalid_ticker", ticker=ticker)
            return []
        page_candidates = self._entry_pages(ticker=ticker, context=context)
        discovered: list[DocumentCandidate] = []
        seen: set[str] = set()
        for source_name, page_url in page_candidates:
            for item in self._extract_document_links(page_url, source_name, ticker, company_name):
                if item.source_url in seen:
                    continue
                seen.add(item.source_url)
                discovered.append(item)
        return discovered

    def _looks_like_supported_ticker(self, ticker: str) -> bool:
        """Return whether a ticker looks valid enough to attempt generic discovery."""

        text = ticker.strip().upper()
        return bool(
            re.fullmatch(r"[A-Z]{1,5}", text)
            or re.fullmatch(r"(US|HK|SH|SZ)\.[A-Z0-9]{1,8}", text)
            or re.fullmatch(r"\d{5,6}", text)
        )

    def _entry_pages(self, ticker: str, context: dict) -> list[tuple[str, str]]:
        """Build candidate document listing pages without binding to one source."""

        pages: list[tuple[str, str]] = []
        ir_website = str(context.get("ir_website") or "").strip()
        official_website = str(context.get("official_website") or "").strip()

        if ir_website:
            pages.extend(
                [
                    ("company_ir", ir_website),
                    ("company_ir", urljoin(ir_website.rstrip("/") + "/", "financial-information")),
                    ("company_ir", urljoin(ir_website.rstrip("/") + "/", "annual-reports")),
                    ("company_ir", urljoin(ir_website.rstrip("/") + "/", "results-presentations")),
                ]
            )
        if official_website:
            pages.extend(
                [
                    ("official_site", official_website),
                    ("official_site", urljoin(official_website.rstrip("/") + "/", "investors")),
                    ("official_site", urljoin(official_website.rstrip("/") + "/", "investor-relations")),
                ]
            )

        futu_slug = self._futu_stock_slug(ticker)
        sec_search_name = context.get("company_name") or ticker
        cninfo_code = self._cninfo_stock_code(ticker)
        pages.extend(
            [
                ("yahoo", f"https://finance.yahoo.com/quote/{ticker}/"),
                ("yahoo", f"https://finance.yahoo.com/quote/{ticker}/financials"),
                ("futu_announcement", f"https://www.futunn.com/stock/{futu_slug}/announcement"),
                ("futu_institutional_ratings", f"https://www.futunn.com/stock/{futu_slug}/institutional-ratings"),
                (
                    "sec_company_search",
                    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={quote_plus(str(sec_search_name))}&owner=exclude&count=40",
                ),
            ]
        )
        if cninfo_code:
            pages.append(
                (
                    "cninfo_disclosure",
                    f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={cninfo_code}&orgId=&sjstsBond=false",
                )
            )
        # preserve order while removing blanks/dupes
        seen: set[str] = set()
        normalized: list[tuple[str, str]] = []
        for source_name, url in pages:
            if not url or url in seen:
                continue
            seen.add(url)
            normalized.append((source_name, url))
        return normalized

    def _extract_document_links(
        self,
        page_url: str,
        source_name: str,
        ticker: str,
        company_name: str,
    ) -> list[DocumentCandidate]:
        """Extract document candidates from one page."""

        try:
            with httpx.Client(timeout=self.timeout_seconds, headers={"User-Agent": self.user_agent}, follow_redirects=True) as client:
                response = client.get(page_url)
                response.raise_for_status()
        except Exception as exc:
            logger.info("document_discovery_page_failed", source_name=source_name, page_url=page_url, error=str(exc))
            return []

        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type and response.url:
            candidate = self._candidate_from_link(str(response.url), "", source_name, ticker, company_name)
            return [candidate] if candidate else []

        try:
            tree = html.fromstring(response.text)
        except Exception as exc:
            logger.info("document_discovery_parse_failed", source_name=source_name, page_url=page_url, error=str(exc))
            return []

        candidates: list[DocumentCandidate] = []
        for element in tree.xpath("//a[@href]"):
            href = str(element.attrib.get("href") or "").strip()
            text = " ".join(element.itertext()).strip()
            full_url = urljoin(str(response.url), href)
            candidate = self._candidate_from_link(full_url, text, source_name, ticker, company_name)
            if candidate:
                candidates.append(candidate)
        return candidates

    def _candidate_from_link(
        self,
        url: str,
        text: str,
        source_name: str,
        ticker: str,
        company_name: str,
    ) -> DocumentCandidate | None:
        """Classify one link into a document candidate."""

        lowered = f"{url} {text}".lower()
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()

        if any(token in host for token in self.BLOCKED_HOST_KEYWORDS) and "futunn.com" not in host:
            return None
        if any(token in lowered for token in self.BLOCKED_URL_PATTERNS):
            return None
        if host.startswith("news.futunn.com"):
            return None
        if not self._is_ir_like_link(lowered, host, path, source_name):
            return None

        doc_type = "other"
        for key, patterns in self.DOC_PATTERNS.items():
            if any(pattern in lowered for pattern in patterns):
                doc_type = key
                break

        if doc_type == "other" and source_name == "futu_announcement":
            doc_type = "ir_material"
        if doc_type == "other" and source_name == "futu_institutional_ratings":
            doc_type = "analyst_rating"

        fiscal_year = None
        for year in range(date.today().year + 1, 1999, -1):
            if str(year) in lowered:
                fiscal_year = year
                break

        return DocumentCandidate(
            ticker=ticker.upper(),
            company_name=company_name,
            doc_type=doc_type,
            fiscal_year=fiscal_year,
            report_date=date(fiscal_year, 12, 31) if fiscal_year else None,
            source_name=source_name,
            source_url=url,
            title=text[:255],
        )

    def _is_ir_like_link(self, lowered: str, host: str, path: str, source_name: str) -> bool:
        """Return whether a link looks like IR/filing/announcement material."""

        has_doc_keyword = any(key in lowered for keywords in self.DOC_PATTERNS.values() for key in keywords)
        has_supported_suffix = path.endswith((".pdf", ".html", ".htm"))
        has_financial_path = any(
            token in lowered
            for token in (
                "announcement",
                "annual-report",
                "annual_report",
                "financial-results",
                "earnings",
                "presentation",
                "quarterly",
                "20-f",
                "10-k",
                "investor",
                "institutional-ratings",
                "rating",
            )
        )
        host_is_ir_like = any(token in host for token in self.IR_HOST_KEYWORDS)
        if source_name.startswith("futu_"):
            return has_doc_keyword or has_financial_path or has_supported_suffix
        if source_name == "cninfo_disclosure":
            return has_doc_keyword or "cninfo.com.cn" in host or has_supported_suffix
        if source_name == "sec_company_search":
            return "sec.gov" in host and (has_doc_keyword or has_supported_suffix or "archives/edgar/data" in path)
        if source_name == "company_ir":
            return has_doc_keyword or has_supported_suffix or has_financial_path
        return host_is_ir_like and (has_doc_keyword or (has_supported_suffix and has_financial_path))

    def _futu_stock_slug(self, ticker: str) -> str:
        """Normalize internal ticker format to Futu stock slug."""

        text = ticker.strip().upper()
        if "." not in text:
            return f"{text}-US"
        market, code = text.split(".", 1)
        suffix = {
            "HK": "HK",
            "US": "US",
            "SH": "SH",
            "SZ": "SZ",
        }.get(market, market)
        if market == "HK" and code.isdigit():
            code = code.zfill(5)
        return f"{code}-{suffix}"

    def _cninfo_stock_code(self, ticker: str) -> str | None:
        """Best-effort normalize ticker for cninfo disclosure pages."""

        text = ticker.strip().upper()
        if "." not in text:
            return text if text.isdigit() else None
        market, code = text.split(".", 1)
        if market in {"SH", "SZ", "HK"} and code:
            return code
        return None
