"""Tests for PeerSetQualityService."""

from app.services.peer_quality_service import PeerSetQualityService


def _service():
    return PeerSetQualityService()


def test_weakly_related_excluded_from_averages():
    peer_analysis = {
        "peer_details": [
            {"ticker": "US.AMZN", "company_name": "Amazon", "peer_type": "core", "confidence": 0.9},
            {"ticker": "US.JD", "company_name": "JD.com", "peer_type": "core", "confidence": 0.85},
            {"ticker": "US.TMUS", "company_name": "T-Mobile", "peer_type": "weakly_related", "confidence": 0.3},
        ],
    }
    peer_comparison = {
        "metrics": {
            "gross_margin": {"company_value": 0.6, "peer_avg": 0.55},
        },
        "peer_details": {"US.AMZN": {}, "US.JD": {}, "US.TMUS": {}},
    }

    result = _service().assess(peer_analysis, peer_comparison, target_ticker="US.PDD")
    pq = result["peer_set_quality"]

    assert len(pq["weakly_related_peers"]) == 1
    assert pq["weakly_related_peers"][0]["ticker"] == "US.TMUS"
    assert len(pq["excluded_from_averages"]) >= 1
    assert pq["excluded_from_averages"][0]["ticker"] == "US.TMUS"

    filtered = result["filtered_peer_comparison"]
    assert "US.TMUS" in filtered.get("excluded_peers", [])


def test_invalid_peer_flagged_telecom_for_ecommerce():
    """T-Mobile should be flagged as invalid for an e-commerce company like PDD."""
    peer_analysis = {
        "peer_details": [
            {"ticker": "US.TMUS", "company_name": "T-Mobile", "peer_type": "secondary", "confidence": 0.5},
        ],
        "peer_metric_snapshots": {
            "US.PDD": {"industry": "Internet Retail"},
            "US.TMUS": {"industry": "Telecom Services"},
        },
    }

    result = _service().assess(peer_analysis, {}, target_ticker="US.PDD")
    pq = result["peer_set_quality"]
    assert len(pq["flags"]) == 1
    assert pq["flags"][0]["ticker"] == "US.TMUS"
    assert pq["weakly_related_peers"][0]["ticker"] == "US.TMUS"


def test_quality_score_all_core():
    peer_analysis = {
        "peer_details": [
            {"ticker": "A", "company_name": "A", "peer_type": "core", "confidence": 0.9},
            {"ticker": "B", "company_name": "B", "peer_type": "core", "confidence": 0.8},
        ],
    }
    result = _service().assess(peer_analysis, {})
    assert result["peer_set_quality"]["quality_score"] >= 0.7
    assert result["peer_set_quality"]["quality_label"] == "good"


def test_quality_score_all_weakly_related():
    peer_analysis = {
        "peer_details": [
            {"ticker": "X", "company_name": "X", "peer_type": "weakly_related", "confidence": 0.2},
            {"ticker": "Y", "company_name": "Y", "peer_type": "weakly_related", "confidence": 0.1},
        ],
    }
    result = _service().assess(peer_analysis, {})
    assert result["peer_set_quality"]["quality_label"] == "poor"
    assert result["peer_set_quality"]["quality_score"] < 0.3


def test_quality_score_empty_peers():
    result = _service().assess({}, {})
    assert result["peer_set_quality"]["quality_score"] == 0.0


def test_infer_type_low_confidence():
    peer_analysis = {
        "peer_details": [
            {"ticker": "Z", "company_name": "Z", "confidence": 0.1, "similarity_dimensions": []},
        ],
    }
    result = _service().assess(peer_analysis, {})
    assert result["peer_set_quality"]["weakly_related_peers"][0]["ticker"] == "Z"


def test_filter_comparison_preserves_when_no_exclusions():
    peer_analysis = {
        "peer_details": [
            {"ticker": "A", "company_name": "A", "peer_type": "core", "confidence": 0.9},
        ],
    }
    comparison = {"metrics": {"gm": {"company_value": 0.5}}, "peer_details": {"A": {}}}
    result = _service().assess(peer_analysis, comparison)
    assert result["filtered_peer_comparison"] == comparison
