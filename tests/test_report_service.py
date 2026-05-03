"""Tests for ReportService."""

from app.services.report_service import ReportService


def _full_state() -> dict:
    return {
        "ticker": "NVDA",
        "company_name": "NVIDIA",
        "company_profile": {
            "business_model": "GPU computing",
            "market_space": "AI/HPC",
            "competition_landscape": "AMD, Intel",
        },
        "peer_list": [{"ticker": "AMD", "company_name": "AMD"}],
        "company_news": [],
        "peer_news": [],
        "event_list": [],
        "financial_summary": {"revenue_cagr": 0.25, "narrative": ["Strong revenue growth"]},
        "peer_comparison": {"summary": ["Outperforms peers on margin"]},
        "financial_red_flags": [],
        "valuation_result": {
            "value_investing": {"bull": 150, "base": 120, "bear": 90},
            "growth": {"bull": 180, "base": 140, "bear": 100},
            "blended": {"bull": 165, "base": 130, "bear": 95},
        },
        "valuation_assumptions": {"discount_rate": 0.1},
        "risk_assessment": {
            "overall_risk_score": 3.5,
            "recommendation": "watch",
            "key_risks": ["High valuation"],
            "monitoring_signals": ["Track margin trend"],
            "red_flags": [],
        },
        "errors": [],
    }


def test_report_returns_markdown_json_and_rating() -> None:
    markdown, json_report, rating = ReportService().build(_full_state())
    assert isinstance(markdown, str)
    assert isinstance(json_report, dict)
    assert rating in {"buy", "watch", "avoid", "research_more"}


def test_report_markdown_contains_all_sections() -> None:
    markdown, _, _ = ReportService().build(_full_state())
    for section in [
        "Executive Summary",
        "Business Analysis",
        "Financial Analysis",
        "Peer Comparison",
        "Valuation Analysis",
        "Risk Assessment",
        "Final Recommendation",
    ]:
        assert section in markdown


def test_report_json_has_required_keys() -> None:
    _, json_report, _ = ReportService().build(_full_state())
    assert {"ticker", "executive_summary", "sections", "final_rating"}.issubset(json_report.keys())


def test_report_partial_state_does_not_crash() -> None:
    markdown, json_report, rating = ReportService().build({"ticker": "X", "company_name": "X Corp"})
    assert rating == "research_more"
    assert "X Corp" in markdown
    assert json_report["ticker"] == "X"


def test_report_includes_errors() -> None:
    state = _full_state()
    state["errors"] = ["FinancialAnalyst failed: timeout"]
    markdown, json_report, _ = ReportService().build(state)
    assert "timeout" in markdown
    assert "timeout" in str(json_report)
    assert json_report["final_rating_label"] in {"Buy", "Watch", "Avoid", "Research More"}


def test_report_does_not_let_empty_llm_business_section_overwrite_local_evidence() -> None:
    class _LLM:
        available = True

        def synthesize_report(self, state, model=None):
            return {
                "company_name": "JD",
                "ticker": "HK.09618",
                "executive_summary": "Test summary",
                "business_analysis": {
                    "core_judgement": "Insufficient information to assess JD's business model.",
                    "supporting_facts": [],
                    "open_questions": ["Need more evidence."],
                },
            }

    state = _full_state()
    state["ticker"] = "HK.09618"
    state["company_name"] = "JD"
    state["business_analysis"] = {
        "core_judgement": "JD operates retail, marketplace, logistics, and services businesses based on annual report evidence.",
        "business_model_detail": "Annual report evidence describes JD's business operations.",
        "supporting_evidence_refs": [{"filing_id": 81, "section": "Business"}],
    }

    markdown, json_report, _ = ReportService(llm_service=_LLM()).build(state)

    assert "JD operates retail" in markdown
    assert "Insufficient information to assess JD" not in markdown
    assert "JD operates retail" in str(json_report["sections"]["Business Analysis"])
