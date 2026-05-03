"""Risk service tests."""

from app.services.risk_service import RiskService


def test_risk_service_flags_negative_events() -> None:
    service = RiskService()
    financial_summary = {
        "years": [2020, 2021, 2022, 2023, 2024],
        "revenue_cagr": 0.03,
        "gross_margin_trend": [{"value": 0.6}, {"value": 0.58}, {"value": 0.55}],
        "ocf_vs_net_income": [{"ratio": 0.8}, {"ratio": 0.9}, {"ratio": 0.95}],
    }
    peer_comparison = {
        "metrics": {
            "gross_margin": {"company_value": 0.55, "peer_avg": 0.65},
            "net_margin": {"company_value": 0.12, "peer_avg": 0.18},
            "roe": {"company_value": 0.14, "peer_avg": 0.22},
        }
    }
    events = [{"event_type": "lawsuit", "importance_score": 0.8, "event_summary": "Major lawsuit filed"}]
    valuation_result = {"growth": {"base": 250}, "blended": {"bull": 260, "base": 200}}
    risk = service.assess(financial_summary, peer_comparison, ["Weak demand"], events, valuation_result)
    assert risk["overall_risk_score"] > 4
    assert any(item["rule"] == "lawsuit" for item in risk["supporting_evidence"])
    assert risk["recommendation"] in {"watch", "avoid"}


def test_risk_service_empty_events_no_crash() -> None:
    assessment = RiskService().assess({}, {}, [], [], {})
    assert isinstance(assessment, dict)
    assert "overall_risk_score" in assessment


def test_risk_service_boundary_recommendation() -> None:
    assessment = RiskService().assess(
        {
            "years": [2022, 2023, 2024],
            "revenue_cagr": 0.20,
            "gross_margin_trend": [{"value": 0.6}, {"value": 0.62}, {"value": 0.63}],
            "ocf_vs_net_income": [{"ratio": 1.1}, {"ratio": 1.05}, {"ratio": 1.08}],
        },
        {},
        [],
        [],
        {"blended": {"base": 100}},
    )
    assert assessment["recommendation"] in {"buy", "watch", "avoid", "research_more"}


def test_risk_service_missing_all_inputs_returns_safe_default() -> None:
    assessment = RiskService().assess({}, {}, [], [], {})
    assert assessment["recommendation"] in {"research_more", "watch", "buy", "avoid"}
