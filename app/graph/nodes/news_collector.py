"""NewsCollector node."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.event_extraction_service import EventExtractionService
from app.services.news_service import NewsService


class NewsCollectorNode:
    """Collect company and peer news, then extract events."""

    def __init__(
        self,
        session: Session,
        news_service: NewsService | None = None,
        event_service: EventExtractionService | None = None,
        model: str | None = None,
    ) -> None:
        self.session = session
        self.news_service = news_service or NewsService()
        self.event_service = event_service or EventExtractionService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        errors: list[str] = []
        try:
            company_news, peer_news = self.news_service.collect_news(
                self.session, state["ticker"], state["company_name"], state.get("peer_list", [])
            )
        except Exception as exc:
            company_news, peer_news = [], []
            errors.append(f"NewsCollector failed to collect news: {exc}")
        try:
            event_list = self.event_service.extract_events(company_news + peer_news, model=self.model)
        except Exception as exc:
            event_list = []
            errors.append(f"NewsCollector failed to extract events: {exc}")
        result: dict = {"company_news": company_news, "peer_news": peer_news, "event_list": event_list}
        if errors:
            result["errors"] = state.get("errors", []) + errors
        return result
