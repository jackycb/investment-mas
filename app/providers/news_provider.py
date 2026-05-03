"""News provider interfaces and implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class NewsProvider(ABC):
    """Abstract provider for research news sources."""

    @abstractmethod
    def get_company_news(self, ticker: str, company_name: str) -> list[dict]:
        """Return recent news for a company."""

    @abstractmethod
    def get_peer_news(self, peers: list[dict]) -> list[dict]:
        """Return recent peer news."""


class MockNewsProvider(NewsProvider):
    """Mock news provider for offline execution."""

    def get_company_news(self, ticker: str, company_name: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [
            {
                "ticker": ticker.upper(),
                "title": f"{company_name} raises AI product roadmap targets after strong demand",
                "source_name": "Mock Finance",
                "source_type": "finance_news",
                "url": f"https://example.com/{ticker.lower()}/roadmap",
                "published_at": (now - timedelta(days=3)).isoformat(),
                "summary": "Demand commentary stayed constructive and management highlighted sustained customer interest.",
                "content": "Management discussed strong customer demand, capacity investments, and margin leverage.",
            },
            {
                "ticker": ticker.upper(),
                "title": f"{company_name} faces margin pressure as supply mix shifts",
                "source_name": "Mock Markets",
                "source_type": "finance_news",
                "url": f"https://example.com/{ticker.lower()}/margin-pressure",
                "published_at": (now - timedelta(days=10)).isoformat(),
                "summary": "Investors are monitoring gross margin as product mix normalizes.",
                "content": "Analysts flagged margin pressure and softer near-term expectations in some customer cohorts.",
            },
        ]

    def get_peer_news(self, peers: list[dict]) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [
            {
                "ticker": peer["ticker"],
                "title": f"{peer['company_name']} guides conservatively amid demand slowdown",
                "source_name": "Mock Finance",
                "source_type": "finance_news",
                "url": f"https://example.com/{peer['ticker'].lower()}/guidance",
                "published_at": (now - timedelta(days=5 + index)).isoformat(),
                "summary": "Peer commentary indicates mixed customer digestion and continued competitive intensity.",
                "content": "Management cited cautious customer budgets and emphasized product execution.",
            }
            for index, peer in enumerate(peers)
        ]


class WebNewsProvider(NewsProvider):
    """Fetch live news from Google News RSS only."""

    def __init__(
        self,
        timeout_seconds: float | None = None,
        user_agent: str | None = None,
        max_articles: int | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds or settings.web_request_timeout_seconds
        self.user_agent = user_agent or settings.web_user_agent
        self.max_articles = max_articles or settings.news_fetch_limit

    def get_company_news(self, ticker: str, company_name: str) -> list[dict]:
        return self._dedupe_and_trim(self._fetch_google_news(ticker=ticker, company_name=company_name))

    def get_peer_news(self, peers: list[dict]) -> list[dict]:
        articles: list[dict] = []
        for peer in peers[:3]:
            articles.extend(self._fetch_google_news(ticker=peer["ticker"], company_name=peer["company_name"]))
        return self._dedupe_and_trim(articles)

    def _fetch_google_news(self, ticker: str, company_name: str) -> list[dict]:
        query = quote_plus(f'"{company_name}" {ticker} earnings OR guidance OR filing OR investor')
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        try:
            text = self._http_get(url)
        except Exception as exc:
            logger.warning("google_news_fetch_failed", ticker=ticker, error=str(exc))
            return []
        if not text.strip() or not text.lstrip().startswith("<?xml"):
            logger.warning("google_news_empty_or_invalid", ticker=ticker)
            return []
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            logger.warning("google_news_parse_failed", ticker=ticker, error=str(exc))
            return []

        items: list[dict] = []
        for item in root.findall(".//item")[: self.max_articles]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate")
            source_name = item.findtext("source") or "Google News"
            summary = title.split(" - ")[0] if " - " in title else title
            published_at = None
            if pub_date:
                try:
                    published_at = parsedate_to_datetime(pub_date).isoformat()
                except Exception:
                    published_at = None
            items.append(
                {
                    "ticker": ticker.upper(),
                    "title": title,
                    "source_name": source_name,
                    "source_type": "finance_news",
                    "url": link,
                    "published_at": published_at,
                    "summary": summary,
                    "content": summary,
                }
            )
        return items


    def _http_get(self, url: str) -> str:
        headers = {"User-Agent": self.user_agent, "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9"}
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
        return response.text

    def _dedupe_and_trim(self, articles: list[dict]) -> list[dict]:
        seen: set[str] = set()
        deduped: list[dict] = []
        for article in articles:
            key = article.get("url") or article.get("title") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(article)
        deduped.sort(key=lambda item: item.get("published_at") or "", reverse=True)
        return deduped[: self.max_articles]
