"""Valuation service tests."""

from app.services.valuation_service import ValuationService


def test_valuation_service_generates_positive_output() -> None:
    service = ValuationService()
    summary = {
        "revenue_cagr": 0.18,
        "latest_metrics": {"revenue": 100.0, "free_cash_flow": 20.0, "gross_margin": 0.7, "net_margin": 0.3},
    }
    peer_comparison = {"metrics": {"gross_margin": {"peer_avg": 0.65}}}
    result, assumptions = service.run(summary, peer_comparison)
    assert result["value_investing"]["base"] > 0
    assert result["growth"]["bull"] > result["growth"]["bear"]
    assert assumptions["growth"]["growth_scalar"] > 0


def test_valuation_service_zero_revenue_does_not_crash() -> None:
    result, assumptions = ValuationService().run({"revenue_cagr": 0.0, "latest_metrics": {"revenue": 0, "free_cash_flow": 0}}, {})
    assert isinstance(result, dict)
    assert isinstance(assumptions, dict)


def test_valuation_service_missing_fields_returns_partial_result() -> None:
    result, assumptions = ValuationService().run({}, {})
    assert isinstance(result, dict)
    assert isinstance(assumptions, dict)


def test_valuation_service_negative_fcf_handled() -> None:
    summary = {
        "revenue_cagr": -0.1,
        "latest_metrics": {"revenue": 100_000, "free_cash_flow": -50_000, "net_margin": -0.1},
    }
    result, assumptions = ValuationService().run(summary, {})
    assert isinstance(result, dict)
    assert result["value_investing"]["base"] >= 0
