"""Tests for RatingGuardrailService."""

from app.services.rating_guardrail_service import RatingGuardrailService


def _service():
    return RatingGuardrailService()


def _default_args(**overrides):
    defaults = {
        "proposed_rating": "buy",
        "valuation_audit": {"passed": True, "checks": {"all": True}, "issues": [], "critical_issue_count": 0},
        "peer_quality": {"peer_set_quality": {"quality_score": 0.8, "quality_label": "good"}},
        "financial_summary": {
            "revenue_cagr": 0.15,
            "latest_metrics": {"gross_margin": 0.5, "net_margin": 0.15, "roe": 0.2},
        },
        "risk_assessment": {"overall_risk_score": 3.0, "red_flags": []},
        "business_analysis": {"core_judgement": "Strong business"},
        "segment_analysis": {},
        "culture_analysis": {},
        "report_confidence": 0.8,
    }
    defaults.update(overrides)
    return defaults


def test_buy_allowed_when_all_checks_pass():
    result = _service().apply(**_default_args())
    assert result["final_rating"] == "buy"
    assert result["rating_changed"] is False
    assert len(result["constraints_triggered"]) == 0


def test_buy_capped_to_watch_when_valuation_audit_fails():
    result = _service().apply(**_default_args(
        valuation_audit={"passed": False, "checks": {"a": False}, "issues": ["missing discount rate"], "critical_issue_count": 1},
    ))
    assert result["final_rating"] == "watch"
    assert result["rating_changed"] is True
    assert any(c["rule"] == "valuation_audit_failed" for c in result["constraints_triggered"])


def test_buy_capped_to_research_more_on_critical_valuation_failures():
    result = _service().apply(**_default_args(
        valuation_audit={"passed": False, "checks": {"a": False, "b": False, "c": False}, "issues": ["i1", "i2", "i3"], "critical_issue_count": 3},
    ))
    assert result["final_rating"] == "research_more"
    assert result["rating_changed"] is True


def test_buy_blocked_when_evidence_confidence_low():
    result = _service().apply(**_default_args(report_confidence=0.4))
    assert result["final_rating"] in {"watch", "research_more"}
    assert any(c["rule"] == "low_evidence_confidence" for c in result["constraints_triggered"])


def test_buy_blocked_when_risk_score_high():
    result = _service().apply(**_default_args(
        risk_assessment={"overall_risk_score": 8.0, "red_flags": []},
    ))
    assert result["final_rating"] == "research_more"
    assert any(c["rule"] == "high_overall_risk" for c in result["constraints_triggered"])


def test_watch_not_downgraded_by_guardrails():
    result = _service().apply(**_default_args(
        proposed_rating="watch",
        valuation_audit={"passed": False, "checks": {}, "issues": ["bad"], "critical_issue_count": 5},
    ))
    assert result["final_rating"] == "watch"
    assert result["rating_changed"] is False


def test_avoid_not_changed():
    result = _service().apply(**_default_args(proposed_rating="avoid"))
    assert result["final_rating"] == "avoid"
    assert result["rating_changed"] is False


def test_segment_economics_missing_triggers_constraint():
    result = _service().apply(**_default_args(
        segment_analysis={
            "is_group_company": True,
            "segments": [
                {"segment_name": "Temu", "business_description": "International marketplace"},
                {"segment_name": "Pinduoduo", "revenue_share": 0.7, "business_description": "Domestic platform"},
            ],
        },
    ))
    assert any(c["rule"] == "segment_economics_missing" for c in result["constraints_triggered"])
    assert result["final_rating"] in {"watch", "research_more"}


def test_decision_scores_computed():
    result = _service().apply(**_default_args())
    scores = result["decision_scores"]
    assert "quality_score" in scores
    assert "growth_score" in scores
    assert "valuation_score" in scores
    assert "risk_score" in scores
    assert "evidence_score" in scores


def test_multiple_red_flags_trigger_constraint():
    result = _service().apply(**_default_args(
        risk_assessment={"overall_risk_score": 4.0, "red_flags": ["flag1", "flag2", "flag3"]},
    ))
    assert any(c["rule"] == "multiple_red_flags" for c in result["constraints_triggered"])


def test_peer_quality_poor_triggers_constraint():
    result = _service().apply(**_default_args(
        peer_quality={"peer_set_quality": {"quality_score": 0.1, "quality_label": "poor"}},
    ))
    assert any(c["rule"] == "peer_set_quality_poor" for c in result["constraints_triggered"])


def test_no_valuation_audit_triggers_hard_block():
    result = _service().apply(**_default_args(valuation_audit={}))
    assert result["final_rating"] == "research_more"
    assert any(c["rule"] == "valuation_not_auditable" for c in result["constraints_triggered"])
