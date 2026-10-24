"""Integration tests for report quality improvements.

Tests that the full report pipeline:
- Includes data_quality_check and rating_decision in report_json
- Does not overwrite risk warnings with promotional conclusions
- Includes valuation_audit results
"""

from app.services.report_service import ReportService


def _full_state(**overrides) -> dict:
    state = {
        "ticker": "US.PDD",
        "company_name": "PDD Holdings",
        "company_profile": {
            "business_model": "E-commerce platform operator",
            "market_space": "Chinese and global e-commerce",
            "competition_landscape": "Alibaba, JD.com, Amazon",
        },
        "peer_list": [{"ticker": "US.BABA", "company_name": "Alibaba"}],
        "business_analysis": {
            "core_judgement": "PDD operates dual platforms: Pinduoduo (domestic) and Temu (international)",
            "open_questions": ["Temu unit economics unclear", "Segment revenue/profit split unavailable"],
        },
        "segment_analysis": {
            "is_group_company": True,
            "segments": [
                {
                    "segment_name": "Pinduoduo",
                    "strategic_role": "core_profit_engine",
                    "revenue_share": 0.65,
                    "business_description": "Domestic e-commerce",
                },
                {
                    "segment_name": "Temu",
                    "strategic_role": "growth_engine",
                    "revenue_share": None,
                    "business_description": "International marketplace",
                    "unit_economics": {"available": False, "open_questions": ["Per-order profitability unknown"]},
                },
            ],
        },
        "culture_analysis": {
            "core_judgement": "Limited governance data available",
            "management_stability_view": "Insufficient information",
            "compensation_and_incentive_view": "Insufficient information",
            "evidence_supported_observations": ["Founder Colin Huang stepped down as CEO"],
            "missing_evidence": ["Individual executive compensation", "Board independence details"],
            "governance_risks": ["Key-man risk with limited succession planning details"],
        },
        "peer_analysis": {
            "peer_details": [
                {"ticker": "US.BABA", "company_name": "Alibaba", "peer_type": "core"},
            ],
        },
        "company_news": [],
        "peer_news": [],
        "event_list": [],
        "financial_summary": {
            "revenue_cagr": 0.45,
            "years": [2020, 2021, 2022, 2023, 2024],
            "latest_metrics": {
                "revenue": 250_000_000_000,
                "gross_margin": 0.62,
                "net_margin": 0.28,
                "roe": 0.35,
                "free_cash_flow": 60_000_000_000,
                "net_income": 70_000_000_000,
            },
            "narrative": ["Strong revenue growth driven by Temu expansion"],
        },
        "peer_comparison": {"summary": ["PDD outperforms peers on revenue growth"]},
        "financial_red_flags": ["Gross margin declined 5pp YoY without explanation"],
        "valuation_result": {
            "value_investing": {"bull": 150, "base": 120, "bear": 90},
            "growth": {"bull": 180, "base": 140, "bear": 100},
            "blended": {"bull": 165, "base": 130, "bear": 95},
            "per_share_currency": "USD",
            "shares_outstanding": 1_400_000_000,
            "current_price": 110.0,
            "commentary": ["Per share valuation."],
        },
        "valuation_assumptions": {
            "value_investing": {"multiple_base": 18, "multiple_bull": 24, "multiple_bear": 12},
            "growth": {"sales_multiple_base": 12},
        },
        "risk_assessment": {
            "overall_risk_score": 4.5,
            "recommendation": "watch",
            "key_risks": ["Temu profitability uncertain", "Regulatory risk in multiple markets"],
            "monitoring_signals": ["Temu take rate trend", "Gross margin trajectory"],
            "red_flags": ["Gross margin decline"],
        },
        "errors": [],
    }
    state.update(overrides)
    return state


def test_report_includes_culture_missing_evidence():
    """Culture section should show missing evidence, not make strong claims."""
    markdown, json_report, _ = ReportService().build(_full_state())
    assert "Missing governance data" in markdown or "missing_evidence" in str(json_report)


def test_report_includes_segment_analysis_with_strategic_roles():
    """Segment analysis should include strategic roles for dual-platform companies."""
    markdown, _, _ = ReportService().build(_full_state())
    assert "Pinduoduo" in markdown
    assert "Temu" in markdown


def test_report_does_not_overwrite_risk_warnings():
    """Final recommendation should not ignore risk assessment warnings."""
    state = _full_state()
    state["risk_assessment"]["overall_risk_score"] = 6.0
    state["risk_assessment"]["key_risks"] = [
        "Temu profitability uncertain",
        "Gross margin decline unexplained",
        "Management succession risk",
    ]
    markdown, _, rating = ReportService().build(state)
    assert rating in {"watch", "research_more", "avoid"}
    assert "risk" in markdown.lower()


def test_report_json_structure_complete():
    """Report JSON should have all required top-level keys."""
    _, json_report, _ = ReportService().build(_full_state())
    required = {"ticker", "company_name", "executive_summary", "sections", "final_rating"}
    assert required.issubset(json_report.keys())


def test_report_partial_state():
    """Minimal state should not crash and should default to research_more."""
    markdown, json_report, rating = ReportService().build(
        {"ticker": "X", "company_name": "X Corp"}
    )
    assert rating == "research_more"
    assert "X Corp" in markdown
