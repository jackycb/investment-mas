"""Tests for structured LLM prompt templates."""

from __future__ import annotations

import json

from app.services.llm_prompt_templates import (
    COMMON_JSON_RULES,
    build_company_profile_messages,
    build_investment_report_messages,
    build_news_analysis_messages,
    build_peer_explanation_messages,
)


def test_common_json_rules_contains_no_fabrication_rule() -> None:
    assert "Do NOT introduce facts" in COMMON_JSON_RULES
    assert "Return exactly one valid JSON object" in COMMON_JSON_RULES


def test_company_profile_messages_use_system_and_json_user_payload() -> None:
    messages = build_company_profile_messages("NVIDIA", "NVDA", {"wikipedia_summary": {"extract": "GPU company"}})
    assert messages[0]["role"] == "system"
    assert "Only extract and summarize from the provided input data" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload["ticker"] == "NVDA"
    assert "business_model" in payload["task"]


def test_peer_explanation_messages_include_similarity_dimensions_schema() -> None:
    messages = build_peer_explanation_messages("Tencent", "HK.00700", {"business_model": "Platform"}, [{"ticker": "HK.09888"}])
    payload = json.loads(messages[1]["content"])
    assert payload["peer_candidates"][0]["ticker"] == "HK.09888"
    assert "similarity_dimensions" in payload["task"]


def test_news_analysis_messages_include_event_schema_rules() -> None:
    messages = build_news_analysis_messages("Tencent", "HK.00700", [{"news_id": "1", "title": "Test news"}])
    assert "Allowed event_type values" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload["news_list"][0]["news_id"] == "1"
    assert "evidence_span" in payload["task"]


def test_investment_report_messages_include_rating_rules_and_research_more_fallback() -> None:
    messages = build_investment_report_messages(
        company_name="Tencent",
        ticker="HK.00700",
        company_profile={},
        business_analysis={"core_judgement": "Annual report evidence supports the business model."},
        segment_analysis={"segments": [{"segment_name": "Retail"}]},
        culture_analysis={"core_judgement": "Governance evidence exists."},
        filing_coverage={"covered": {"business_model": {}}},
        financial_summary={},
        peer_comparison={},
        financial_red_flags=[],
        valuation_result={},
        valuation_assumptions={},
        risk_assessment={},
        event_list=[],
    )
    assert "research_more" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload["ticker"] == "HK.00700"
    assert payload["business_analysis"]["core_judgement"].startswith("Annual report evidence")
    assert "business_model" in payload["filing_coverage"]["covered"]
    assert "final_rating" in payload["task"]
    assert "Do not downgrade" in payload["task"]
