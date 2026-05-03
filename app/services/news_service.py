"""News collection and summarization service."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories.company_repository import get_company_by_ticker
from app.db.repositories.news_repository import get_recent_articles
from app.providers.news_provider import MockNewsProvider, NewsProvider, WebNewsProvider

settings = get_settings()


def _looks_like_supported_ticker(ticker: str) -> bool:
    """Return whether a ticker looks valid enough for web news retrieval."""

    text = ticker.strip().upper()
    return bool(
        re.fullmatch(r"[A-Z]{1,5}", text)
        or re.fullmatch(r"(US|HK|SH|SZ)\.[A-Z0-9]{1,8}", text)
        or re.fullmatch(r"\d{5,6}", text)
    )


class NewsService:
    """Fetch company and peer news from storage or web providers.

    This service is responsible only for retrieval, normalization, and reuse of
    fetched articles. Summarization and event extraction happen downstream.
    """

    def __init__(self, provider: NewsProvider | None = None) -> None:
        if provider is not None:
            self.provider = provider
        else:
            self.provider = self._build_provider()

    def _build_provider(self) -> NewsProvider:
        """Build the configured news provider."""

        if settings.news_provider.lower() == "web":
            return WebNewsProvider()
        if settings.news_provider.lower() == "mock":
            return MockNewsProvider()
        return MockNewsProvider()

    def collect_news(self, session: Session | None, ticker: str, company_name: str, peers: list[dict]) -> tuple[list[dict], list[dict]]:
        """Collect recent company and peer news."""

        company_news = self._collect_for_target(session, ticker, company_name, peer_mode=False)
        peer_news: list[dict] = []
        for peer in peers:
            peer_ticker = str(peer.get("ticker", "")).upper().strip()
            peer_name = peer.get("company_name") or peer_ticker
            if not peer_ticker:
                continue
            peer_news.extend(self._collect_for_target(session, peer_ticker, peer_name, peer_mode=True))
        return company_news, peer_news

    def _collect_for_target(self, session: Session | None, ticker: str, company_name: str, peer_mode: bool) -> list[dict]:
        """Collect news for a single company, preferring stored articles."""

        if session is not None:
            company = get_company_by_ticker(session, ticker)
            if company:
                articles = get_recent_articles(session, company.id, limit=10)
                if articles:
                    return [
                        {
                            "ticker": ticker.upper(),
                            "title": article.title,
                            "source_name": article.source_name,
                            "source_type": article.source_type,
                            "url": article.url,
                            "published_at": article.published_at.isoformat() if article.published_at else None,
                            "summary": article.summary,
                            "content": article.content,
                            "author": article.author,
                            "language": article.language,
                        }
                        for article in articles
                    ]
        if not _looks_like_supported_ticker(ticker):
            return []
        if peer_mode:
            return self.provider.get_peer_news([{"ticker": ticker.upper(), "company_name": company_name}])
        return self.provider.get_company_news(ticker, company_name)
