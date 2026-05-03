"""Tests for EventExtractionService."""

from unittest.mock import MagicMock

from app.services.event_extraction_service import EventExtractionService


def _article(title: str, summary: str = "") -> dict:
    return {"title": title, "summary": summary or title, "ticker": "NVDA", "published_at": "2024-06-01"}


def _service() -> EventExtractionService:
    llm = MagicMock()
    llm.available = False
    return EventExtractionService(llm_service=llm)


def test_guidance_cut_detected() -> None:
    service = _service()
    events = service.extract_events([_article("Company cuts guidance amid slowing demand", "guidance reset")])
    assert "guidance_cut" in [event["event_type"] for event in events]


def test_lawsuit_detected() -> None:
    service = _service()
    events = service.extract_events([_article("Firm faces major lawsuit over patent infringement")])
    assert "lawsuit" in [event["event_type"] for event in events]


def test_regulatory_action_detected() -> None:
    service = _service()
    events = service.extract_events([_article("Investigation announced by SEC", "regulatory action underway")])
    assert "regulatory_action" in [event["event_type"] for event in events]


def test_management_change_detected() -> None:
    service = _service()
    events = service.extract_events([_article("CEO change announced after board review")])
    assert "management_change" in [event["event_type"] for event in events]


def test_margin_pressure_detected() -> None:
    service = _service()
    events = service.extract_events([_article("Margin pressure intensifies due to rising costs")])
    assert "margin_pressure" in [event["event_type"] for event in events]


def test_demand_slowdown_detected() -> None:
    service = _service()
    events = service.extract_events([_article("Demand slowdown hits semiconductor sector")])
    assert "demand_slowdown" in [event["event_type"] for event in events]


def test_unmatched_article_returns_other() -> None:
    service = _service()
    events = service.extract_events([_article("Company hosts annual developer conference")])
    assert "other" in [event["event_type"] for event in events]


def test_empty_input_returns_empty_list() -> None:
    assert _service().extract_events([]) == []


def test_event_structure_complete() -> None:
    events = _service().extract_events([_article("Guidance cut announced", "guidance lowered")])
    event = events[0]
    required_keys = {
        "event_type",
        "sentiment",
        "importance_score",
        "confidence_score",
        "risk_tags",
        "entities",
        "event_summary",
        "evidence_span",
        "impact_dimensions",
    }
    assert required_keys.issubset(event.keys())


def test_llm_timeout_falls_back_to_rules() -> None:
    llm = MagicMock()
    llm.available = True
    llm.analyze_news_articles.side_effect = TimeoutError("The read operation timed out")
    service = EventExtractionService(llm_service=llm)

    events = service.extract_events([_article("Company cuts guidance amid slowing demand", "guidance reset")])

    assert events
    assert events[0]["event_type"] == "guidance_cut"
