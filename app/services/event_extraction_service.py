"""Rule-based event extraction for news articles."""

from __future__ import annotations

from datetime import datetime, timezone
import os

from app.core.logging import get_logger
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service


EVENT_RULES: dict[str, dict[str, str | float | list[str]]] = {
    "guidance": {"event_type": "guidance_cut", "sentiment": "negative", "importance_score": 0.75, "risk_tags": ["execution", "demand"]},
    "lawsuit": {"event_type": "lawsuit", "sentiment": "negative", "importance_score": 0.85, "risk_tags": ["legal", "governance"]},
    "investigation": {"event_type": "regulatory_action", "sentiment": "negative", "importance_score": 0.9, "risk_tags": ["regulatory"]},
    "ceo change": {"event_type": "management_change", "sentiment": "neutral", "importance_score": 0.6, "risk_tags": ["leadership"]},
    "margin pressure": {"event_type": "margin_pressure", "sentiment": "negative", "importance_score": 0.7, "risk_tags": ["profitability"]},
    "demand slowdown": {"event_type": "demand_slowdown", "sentiment": "negative", "importance_score": 0.8, "risk_tags": ["demand"]},
}


class EventExtractionService:
    """Extract structured events from already-fetched articles.

    Retrieval stays in the news provider layer. This service only performs
    summarization/event extraction, preferring LLM analysis when available and
    falling back to deterministic keyword rules.
    """

    def __init__(self, llm_service: AliyunLLMService | None = None) -> None:
        self.llm_service = llm_service or get_llm_service()
        self._disable_default_llm_in_pytest = llm_service is None and bool(os.getenv("PYTEST_CURRENT_TEST"))

    def extract_events(self, articles: list[dict], model: str | None = None) -> list[dict]:
        """Return event objects derived from LLM analysis or keyword rules."""

        enriched_articles = self._enrich_articles_with_llm(articles, model=model)
        return self._extract_events_by_rules(enriched_articles)

    def _enrich_articles_with_llm(self, articles: list[dict], model: str | None = None) -> list[dict]:
        """Use LLM to summarize fetched news and pre-label events when available."""

        if not articles or not self.llm_service.available or self._disable_default_llm_in_pytest:
            return articles

        ticker = str(articles[0].get("ticker", ""))
        try:
            analysis = self.llm_service.analyze_news_articles(ticker=ticker, company_name=ticker, articles=articles, model=model)
        except Exception as exc:
            logger.warning("news_event_llm_analysis_failed", ticker=ticker, error=str(exc))
            return articles
        if not analysis:
            return articles

        analysis_by_title = {
            str(item.get("title", "")).strip(): item
            for item in analysis
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        }
        enriched: list[dict] = []
        for article in articles:
            item = analysis_by_title.get(str(article.get("title", "")).strip())
            if not item:
                enriched.append(article)
                continue
            enriched.append(
                {
                    **article,
                    "summary": item.get("summary") or item.get("summary_cn") or article.get("summary"),
                    "llm_events": item.get("events") or [],
                }
            )
        return enriched

    def _extract_events_by_rules(self, articles: list[dict]) -> list[dict]:
        """Build event records from LLM-enriched fields or rule keywords."""

        events: list[dict] = []
        for article in articles:
            llm_events = article.get("llm_events") or []
            if llm_events:
                for llm_event in llm_events[:3]:
                    sentiment = llm_event.get("sentiment") or "neutral"
                    impact_dimensions = llm_event.get("impact_dimensions") or []
                    events.append(
                        {
                            "ticker": article.get("ticker"),
                            "event_date": article.get("published_at") or datetime.now(timezone.utc).date().isoformat(),
                            "event_type": llm_event.get("event_type") or "other",
                            "sentiment": sentiment,
                            "importance_score": llm_event.get("importance_score") or 0.5,
                            "confidence_score": llm_event.get("confidence_score") or 0.65,
                            "risk_tags": llm_event.get("risk_tags") or ["monitoring"],
                            "entities": [article.get("ticker", "")],
                            "event_summary": llm_event.get("event_summary") or article.get("summary") or article.get("title"),
                            "evidence_span": llm_event.get("evidence_span") or "",
                            "impact_dimensions": impact_dimensions,
                        }
                    )
                continue
            haystack = f"{article.get('title', '')} {article.get('summary', '')}".lower()
            matched = False
            for keyword, rule in EVENT_RULES.items():
                if keyword in haystack:
                    events.append(
                        {
                            "ticker": article.get("ticker"),
                            "event_date": article.get("published_at") or datetime.now(timezone.utc).date().isoformat(),
                            "event_type": rule["event_type"],
                            "sentiment": rule["sentiment"],
                            "importance_score": rule["importance_score"],
                            "confidence_score": 0.7,
                            "risk_tags": rule["risk_tags"],
                            "entities": [article.get("ticker", "")],
                            "event_summary": article.get("summary") or article.get("title"),
                            "evidence_span": article.get("title") or article.get("summary") or "",
                            "impact_dimensions": ["fundamentals", "valuation"] if rule["sentiment"] == "negative" else ["monitoring"],
                        }
                    )
                    matched = True
            if not matched:
                events.append(
                    {
                        "ticker": article.get("ticker"),
                        "event_date": article.get("published_at") or datetime.now(timezone.utc).date().isoformat(),
                        "event_type": "other",
                        "sentiment": "neutral",
                        "importance_score": 0.35,
                        "confidence_score": 0.55,
                        "risk_tags": ["monitoring"],
                        "entities": [article.get("ticker", "")],
                        "event_summary": article.get("summary") or article.get("title"),
                        "evidence_span": article.get("title") or article.get("summary") or "",
                        "impact_dimensions": ["monitoring"],
                    }
                )
        return events
logger = get_logger(__name__)