"""Tests for ValuationAuditService."""

from app.services.valuation_audit_service import ValuationAuditService


def _service():
    return ValuationAuditService()


def test_audit_passes_with_complete_data():
    valuation = {
        "blended": {"bear": 50, "base": 80, "bull": 120},
        "value_investing": {"bear": 40, "base": 70, "bull": 110},
        "growth": {"bear": 60, "base": 90, "bull": 130},
        "per_share_currency": "USD",
        "current_price": 75.0,
        "shares_outstanding": 1_000_000,
        "commentary": ["Valuation on a per share basis."],
    }
    assumptions = {
        "value_investing": {"multiple_base": 18, "multiple_bull": 24, "multiple_bear": 12},
        "growth": {"sales_multiple_base": 12},
    }
    financials = {
        "latest_metrics": {"revenue": 500_000_000, "free_cash_flow": 50_000_000, "net_income": 40_000_000},
    }

    result = _service().audit(valuation, assumptions, financials)
    assert result["passed"] is True
    assert result["issue_count"] == 0


def test_audit_fails_missing_currency():
    valuation = {
        "blended": {"bear": 50, "base": 80, "bull": 120},
        "per_share_currency": "",
        "current_price": 75.0,
        "shares_outstanding": 1_000_000,
    }
    result = _service().audit(valuation, {}, {})
    assert result["checks"]["currency_stated"] is False
    assert any("currency" in i.lower() for i in result["issues"])


def test_audit_fails_ambiguous_currency():
    valuation = {
        "blended": {"bear": 50, "base": 80, "bull": 120},
        "per_share_currency": "Reporting currency (market cap)",
        "current_price": 75.0,
    }
    result = _service().audit(valuation, {}, {})
    assert result["checks"]["currency_stated"] is False


def test_audit_caps_buy_when_assumptions_missing():
    """Audit should fail when no discount rate or multiples are found."""
    valuation = {
        "blended": {"bear": 50, "base": 80, "bull": 120},
        "per_share_currency": "USD",
        "current_price": 75.0,
        "shares_outstanding": 1_000_000,
        "commentary": ["Per share valuation."],
    }
    assumptions = {"value_investing": {}, "growth": {}}
    result = _service().audit(valuation, assumptions, {})
    assert result["passed"] is False
    assert result["checks"]["discount_rate_reasonable"] is False


def test_audit_extreme_upside_flagged():
    """Price far below bear-case should trigger extreme upside audit flag."""
    valuation = {
        "blended": {"bear": 200, "base": 300, "bull": 400},
        "per_share_currency": "USD",
        "current_price": 50.0,
        "shares_outstanding": 1_000_000,
        "price_vs_valuation": {"current_price": 50.0},
        "commentary": ["Per share valuation."],
    }
    assumptions = {"value_investing": {"multiple_base": 18}, "growth": {"sales_multiple_base": 12}}
    result = _service().audit(valuation, assumptions, {})
    assert result["checks"]["extreme_upside_audited"] is False
    assert any("extreme upside" in i.lower() for i in result["issues"])


def test_audit_scenario_ordering_violated():
    valuation = {
        "blended": {"bear": 100, "base": 80, "bull": 120},
        "per_share_currency": "USD",
    }
    assumptions = {"value_investing": {"multiple_base": 18}, "growth": {}}
    result = _service().audit(valuation, assumptions, {})
    assert result["checks"]["scenario_ordering"] is False


def test_audit_fcf_unit_mismatch():
    valuation = {"blended": {"bear": 50, "base": 80, "bull": 120}, "per_share_currency": "USD"}
    assumptions = {"value_investing": {"multiple_base": 18}, "growth": {}}
    financials = {"latest_metrics": {"revenue": 100, "free_cash_flow": 500}}
    result = _service().audit(valuation, assumptions, financials)
    assert result["checks"]["fcf_unit_consistent"] is False


def test_audit_empty_valuation():
    result = _service().audit({}, {}, {})
    assert result["passed"] is False
