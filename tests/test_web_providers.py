"""Tests for web-backed providers using mocked network calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.providers.company_info_provider import MockCompanyInfoProvider, WebCompanyInfoProvider
from app.providers.news_provider import WebNewsProvider
from app.services.event_extraction_service import EventExtractionService
from app.services.aliyun_llm_service import reset_llm_service


def test_web_company_info_provider_returns_unavailable_profile_without_web_lookup() -> None:
    reset_llm_service()
    provider = WebCompanyInfoProvider()
    provider.llm = MagicMock()
    provider.llm.available = False
    profile = provider.get_company_profile("HK.00700", "Tencent")
    assert isinstance(profile, dict)
    assert profile.get("raw_sources", [{}])[0].get("source") == "unavailable"
    assert profile.get("business_model") == ""


def test_web_company_info_provider_returns_mock_peers_when_llm_unavailable() -> None:
    reset_llm_service()
    provider = WebCompanyInfoProvider()
    provider.llm = MagicMock()
    provider.llm.available = False
    peers = provider.get_peers("HK.00700", "Tencent")
    expected = MockCompanyInfoProvider().get_peers("HK.00700", "Tencent")
    assert [(item["ticker"], item["company_name"]) for item in peers] == [
        (item["ticker"], item["company_name"]) for item in expected
    ]


def test_web_company_info_provider_does_not_use_llm_generated_peers() -> None:
    reset_llm_service()
    provider = WebCompanyInfoProvider()
    provider.llm = MagicMock()
    provider.llm.available = True
    provider.llm.annotate_peer_candidates.return_value = [
        {
            "ticker": "HK.09888",
            "company_name": "Baidu",
            "peer_type": "core",
            "confidence": 0.81,
            "similarity_dimensions": ["industry", "business_model"],
            "rationale": "搜索与云业务可作为平台同行参考。",
        }
    ]
    peers = provider.get_peers("HK.00700", "Tencent")

    assert peers[0]["ticker"] == "HK.09888"
    assert all(peer["ticker"] != "FAKE" for peer in peers)


def test_web_company_info_provider_peer_annotation_timeout_falls_back() -> None:
    reset_llm_service()
    provider = WebCompanyInfoProvider()
    provider.llm = MagicMock()
    provider.llm.available = True
    provider.llm.annotate_peer_candidates.side_effect = TimeoutError("The read operation timed out")
    peers = provider.get_peers("HK.00700", "Tencent")

    assert peers
    assert peers[0]["ticker"] == "HK.09888"


def test_web_company_info_provider_uses_rule_based_candidates_without_web_fetch() -> None:
    reset_llm_service()
    provider = WebCompanyInfoProvider()
    provider.llm = MagicMock()
    provider.llm.available = False
    peers = provider.get_peers("US.META", "Meta Platforms")
    assert peers
    assert any(peer["ticker"] == "GOOGL" for peer in peers)


def test_web_company_info_provider_uses_curated_fallback_for_hk_00883() -> None:
    reset_llm_service()
    provider = WebCompanyInfoProvider()
    provider.llm = MagicMock()
    provider.llm.available = False
    profile = provider.get_company_profile("HK.00883", "CNOOC")
    assert profile["raw_sources"][0]["source"] == "curated_fallback"
    assert "oil" in profile["business_model"].lower() or "offshore" in profile["business_model"].lower()


def test_google_news_parse_empty_xml_returns_empty_list() -> None:
    provider = WebNewsProvider()
    with patch.object(provider, "_http_get", return_value="not valid xml"):
        result = provider.get_company_news("NVDA", "NVIDIA")
    assert result == []


def test_web_news_provider_uses_google_news_only_for_company() -> None:
    provider = WebNewsProvider()
    provider._http_get = MagicMock(return_value="<?xml version='1.0'?><rss><channel></channel></rss>")
    provider.get_company_news("HK.00700", "Tencent")
    provider._http_get.assert_called_once()


def test_news_provider_dedupe_removes_duplicate_urls() -> None:
    provider = WebNewsProvider()
    articles = [
        {"url": "https://example.com/a", "title": "Article A", "published_at": "2024-01-01"},
        {"url": "https://example.com/a", "title": "Article A Dup", "published_at": "2024-01-02"},
        {"url": "https://example.com/b", "title": "Article B", "published_at": "2024-01-03"},
    ]
    result = provider._dedupe_and_trim(articles)
    assert len(result) == 2


def test_web_news_provider_parses_google_news_rss() -> None:
    provider = WebNewsProvider()
    provider._http_get = MagicMock(
        return_value="<?xml version='1.0' encoding='UTF-8'?><rss><channel><item><title>NVIDIA gains after earnings - CNBC</title><link>https://example.com/article</link><pubDate>Fri, 25 Apr 2026 10:00:00 GMT</pubDate><source>CNBC</source></item></channel></rss>"
    )
    items = provider.get_company_news("NVDA", "NVIDIA")
    assert len(items) == 1
    assert items[0]["source_name"] == "CNBC"
    assert items[0]["source_type"] == "finance_news"


def test_event_extraction_service_uses_llm_only_after_articles_are_fetched() -> None:
    llm = MagicMock()
    llm.available = True
    llm.analyze_news_articles.return_value = [
        {
            "news_id": "1",
            "title": "Company cuts guidance",
            "summary_cn": "公司下调指引，市场担忧需求放缓。",
            "events": [
                {
                    "event_type": "guidance_cut",
                    "sentiment": "negative",
                    "importance_score": 0.8,
                    "confidence_score": 0.7,
                    "risk_tags": ["demand"],
                    "impact_dimensions": ["revenue", "demand"],
                    "event_summary": "公司下调全年指引。",
                    "evidence_span": "cuts guidance",
                }
            ],
        }
    ]
    service = EventExtractionService(llm_service=llm)

    events = service.extract_events(
        [
            {
                "ticker": "TEST",
                "title": "Company cuts guidance",
                "summary": "guidance lowered",
                "published_at": "2026-04-21T00:00:00+00:00",
            }
        ]
    )

    llm.analyze_news_articles.assert_called_once()
    assert events[0]["event_type"] == "guidance_cut"
    assert events[0]["event_summary"] == "公司下调全年指引。"
