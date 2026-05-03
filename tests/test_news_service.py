"""Tests for NewsService."""

from app.providers.news_provider import MockNewsProvider
from app.services.news_service import NewsService


def test_collect_news_returns_company_and_peer_news() -> None:
    service = NewsService(provider=MockNewsProvider())
    company_news, peer_news = service.collect_news(
        session=None,
        ticker="NVDA",
        company_name="NVIDIA",
        peers=[{"ticker": "AMD", "company_name": "AMD"}],
    )
    assert isinstance(company_news, list)
    assert isinstance(peer_news, list)
    assert len(company_news) > 0


def test_collect_news_empty_peers_returns_empty_peer_news() -> None:
    service = NewsService(provider=MockNewsProvider())
    company_news, peer_news = service.collect_news(session=None, ticker="NVDA", company_name="NVIDIA", peers=[])
    assert isinstance(peer_news, list)
    assert len(peer_news) == 0

