"""Peer set quality service — validate and classify peer sets for report quality."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class PeerSetQualityService:
    """Assess peer set quality and filter weakly_related peers from quantitative analysis.

    Operates on the peer_analysis output from PeerAnalyst and peer_comparison
    from FinancialAnalyst, adding quality metadata and filtered averages.
    """

    def assess(
        self,
        peer_analysis: dict[str, Any],
        peer_comparison: dict[str, Any],
        target_ticker: str = "",
    ) -> dict[str, Any]:
        peer_details = peer_analysis.get("peer_details", [])
        if not peer_details:
            peer_details = peer_analysis.get("peer_analysis", [])

        classified = self._classify_peers(peer_details)
        flagged = self._flag_invalid_peers(classified, peer_analysis, target_ticker)
        quality_score = self._compute_quality_score(classified)
        filtered_comparison = self._filter_comparison(
            peer_comparison, classified, target_ticker
        )

        return {
            "peer_set_quality": {
                "quality_score": quality_score,
                "quality_label": self._quality_label(quality_score),
                "total_peers": len(classified),
                "core_peers": [p for p in classified if p.get("effective_type") == "core"],
                "secondary_peers": [p for p in classified if p.get("effective_type") == "secondary"],
                "valuation_peers": [p for p in classified if p.get("effective_type") == "valuation"],
                "weakly_related_peers": [p for p in classified if p.get("effective_type") == "weakly_related"],
                "excluded_from_averages": [p for p in classified if p.get("excluded_from_averages")],
                "flags": flagged,
            },
            "filtered_peer_comparison": filtered_comparison,
        }

    def _classify_peers(self, peer_details: list[dict]) -> list[dict]:
        """Ensure every peer has an effective_type and exclusion flag."""
        result = []
        for peer in peer_details:
            p = dict(peer)
            peer_type = p.get("peer_type", "").lower()
            if peer_type not in ("core", "secondary", "valuation", "weakly_related"):
                peer_type = self._infer_type(p)
            p["effective_type"] = peer_type
            p["excluded_from_averages"] = peer_type == "weakly_related"
            result.append(p)
        return result

    def _infer_type(self, peer: dict) -> str:
        """Heuristic classification when peer_type is missing."""
        confidence = peer.get("confidence", 0.5)
        dims = peer.get("similarity_dimensions", [])
        rationale = str(peer.get("rationale", "")).lower()

        if confidence < 0.3:
            return "weakly_related"

        business_dims = {"product", "business_model", "customer"}
        if business_dims.intersection(set(dims)):
            return "core" if confidence >= 0.7 else "secondary"

        if "valuation_comparable" in dims:
            return "valuation"

        if any(term in rationale for term in ("different industry", "unrelated", "limited relevance", "weak")):
            return "weakly_related"

        return "secondary"

    def _flag_invalid_peers(
        self,
        classified: list[dict],
        peer_analysis: dict,
        target_ticker: str,
    ) -> list[dict]:
        """Flag peers that are likely invalid for the target company."""
        flags: list[dict] = []
        snapshots = peer_analysis.get("peer_metric_snapshots", {})

        for peer in classified:
            ticker = peer.get("ticker", "")
            name = peer.get("company_name", "")
            rationale = str(peer.get("rationale", "")).lower()
            industry = ""

            if ticker in snapshots:
                industry = str(snapshots[ticker].get("industry", "")).lower()

            if self._is_likely_invalid(peer, target_ticker, industry, snapshots):
                peer["effective_type"] = "weakly_related"
                peer["excluded_from_averages"] = True
                flags.append({
                    "ticker": ticker,
                    "company_name": name,
                    "reason": f"Flagged as likely invalid peer: industry mismatch or low confidence",
                    "original_type": peer.get("peer_type", "unknown"),
                })
                logger.warning(
                    "peer_flagged_invalid",
                    peer=ticker,
                    target=target_ticker,
                    reason="industry_mismatch_or_low_confidence",
                )

        return flags

    def _is_likely_invalid(
        self,
        peer: dict,
        target_ticker: str,
        peer_industry: str,
        snapshots: dict,
    ) -> bool:
        """Heuristic check for invalid peers."""
        target_industry = ""
        if target_ticker in snapshots:
            target_industry = str(snapshots[target_ticker].get("industry", "")).lower()

        if not peer_industry or not target_industry:
            return False

        telecom_terms = {"telecom", "wireless", "mobile carrier", "telephone"}
        ecommerce_terms = {"e-commerce", "ecommerce", "internet retail", "online retail", "marketplace"}
        tech_terms = {"technology", "software", "internet", "digital"}

        peer_is_telecom = any(t in peer_industry for t in telecom_terms)
        target_is_ecommerce = any(t in target_industry for t in ecommerce_terms)
        target_is_tech = any(t in target_industry for t in tech_terms)

        if peer_is_telecom and (target_is_ecommerce or target_is_tech):
            return True

        return False

    def _compute_quality_score(self, classified: list[dict]) -> float:
        """Score from 0.0-1.0 based on peer set composition."""
        if not classified:
            return 0.0

        core_count = sum(1 for p in classified if p["effective_type"] == "core")
        secondary_count = sum(1 for p in classified if p["effective_type"] == "secondary")
        weak_count = sum(1 for p in classified if p["effective_type"] == "weakly_related")
        total = len(classified)

        usable_ratio = (core_count + secondary_count) / total if total > 0 else 0
        core_bonus = min(core_count * 0.15, 0.3)

        score = usable_ratio * 0.7 + core_bonus
        if weak_count > 0 and weak_count >= total / 2:
            score *= 0.5

        return round(min(1.0, max(0.0, score)), 2)

    def _quality_label(self, score: float) -> str:
        if score >= 0.7:
            return "good"
        if score >= 0.4:
            return "fair"
        return "poor"

    def _filter_comparison(
        self,
        peer_comparison: dict[str, Any],
        classified: list[dict],
        target_ticker: str,
    ) -> dict[str, Any]:
        """Recompute peer averages excluding weakly_related peers."""
        excluded_tickers = {
            p["ticker"].upper()
            for p in classified
            if p.get("excluded_from_averages")
        }

        if not excluded_tickers:
            return peer_comparison

        filtered = dict(peer_comparison)
        metrics = peer_comparison.get("metrics", {})
        if not metrics:
            return filtered

        new_metrics: dict[str, Any] = {}
        included_tickers: list[str] = []

        peer_details = peer_comparison.get("peer_details", [])
        for entry in peer_details:
            t = entry.get("ticker", "") if isinstance(entry, dict) else str(entry)
            if t.upper() not in excluded_tickers and t.upper() != target_ticker.upper():
                included_tickers.append(t)

        for metric_name, metric_data in metrics.items():
            if not isinstance(metric_data, dict):
                new_metrics[metric_name] = metric_data
                continue

            new_entry = dict(metric_data)
            new_entry["included_peers"] = included_tickers
            new_entry["excluded_peers"] = list(excluded_tickers)
            new_metrics[metric_name] = new_entry

        filtered["metrics"] = new_metrics
        filtered["peer_filter_applied"] = True
        filtered["included_peers"] = included_tickers
        filtered["excluded_peers"] = list(excluded_tickers)

        return filtered
