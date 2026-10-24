"""Rating guardrail service — enforce constraints on final investment rating."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

ALLOWED_RATINGS = {"buy", "watch", "avoid", "research_more"}


class RatingGuardrailService:
    """Deterministic post-LLM guardrails that may downgrade a rating.

    The service never *upgrades* a rating — it only constrains "buy" to
    "watch" or "research_more" when hard evidence thresholds are not met.
    """

    def apply(
        self,
        proposed_rating: str,
        valuation_audit: dict[str, Any],
        peer_quality: dict[str, Any],
        financial_summary: dict[str, Any],
        risk_assessment: dict[str, Any],
        business_analysis: dict[str, Any],
        segment_analysis: dict[str, Any],
        culture_analysis: dict[str, Any],
        report_confidence: float | None = None,
    ) -> dict[str, Any]:
        proposed = proposed_rating.lower() if proposed_rating else "research_more"
        if proposed not in ALLOWED_RATINGS:
            proposed = "research_more"

        constraints: list[dict[str, str]] = []

        if proposed == "buy":
            self._check_valuation_auditable(valuation_audit, constraints)
            self._check_peer_quality(peer_quality, constraints)
            self._check_segment_economics(business_analysis, segment_analysis, constraints)
            self._check_evidence_confidence(report_confidence, risk_assessment, constraints)
            self._check_red_flags(risk_assessment, financial_summary, constraints)

        final_rating = proposed
        if constraints and proposed == "buy":
            has_hard_block = any(c["severity"] == "hard" for c in constraints)
            final_rating = "research_more" if has_hard_block else "watch"
            logger.warning(
                "rating_guardrail_triggered",
                proposed=proposed,
                final=final_rating,
                constraint_count=len(constraints),
                constraints=[c["rule"] for c in constraints],
            )

        scores = self._compute_decision_scores(
            financial_summary, risk_assessment, valuation_audit, peer_quality, report_confidence
        )

        return {
            "proposed_rating": proposed_rating,
            "final_rating": final_rating,
            "rating_changed": final_rating != proposed,
            "constraints_triggered": constraints,
            "decision_scores": scores,
        }

    def _check_valuation_auditable(
        self, audit: dict[str, Any], constraints: list
    ) -> None:
        if not audit or not audit.get("checks"):
            constraints.append({
                "rule": "valuation_not_auditable",
                "severity": "hard",
                "reason": "No valuation audit was performed — cannot confirm Buy",
            })
            return

        if not audit.get("passed"):
            critical = audit.get("critical_issue_count", 0)
            if critical >= 3:
                constraints.append({
                    "rule": "valuation_audit_critical_failures",
                    "severity": "hard",
                    "reason": f"Valuation audit has {critical} critical issues: {'; '.join(audit.get('issues', [])[:3])}",
                })
            else:
                constraints.append({
                    "rule": "valuation_audit_failed",
                    "severity": "soft",
                    "reason": f"Valuation audit failed with {critical} critical issue(s)",
                })

    def _check_peer_quality(
        self, peer_quality: dict[str, Any], constraints: list
    ) -> None:
        pq = peer_quality.get("peer_set_quality", {})
        score = pq.get("quality_score", 1.0)
        label = pq.get("quality_label", "good")

        if label == "poor" or score < 0.3:
            constraints.append({
                "rule": "peer_set_quality_poor",
                "severity": "soft",
                "reason": f"Peer set quality is '{label}' (score={score:.2f}) — comparative analysis unreliable",
            })

    def _check_segment_economics(
        self, business_analysis: dict, segment_analysis: dict, constraints: list
    ) -> None:
        """Flag if thesis-critical segment economics are missing."""
        segments = segment_analysis.get("segments", []) if isinstance(segment_analysis, dict) else []
        is_group = segment_analysis.get("is_group_company", False) if isinstance(segment_analysis, dict) else False

        if not is_group and not segments:
            return

        segments_missing_economics = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            name = seg.get("segment_name", "Unknown")
            has_rev = seg.get("revenue_share") is not None
            has_desc = bool(seg.get("business_description"))
            if not has_rev and has_desc:
                segments_missing_economics.append(name)

        open_questions = []
        if isinstance(business_analysis, dict):
            open_questions = business_analysis.get("open_questions", [])

        has_unit_economics_gap = any(
            "unit econom" in str(q).lower() or "segment econom" in str(q).lower()
            for q in open_questions
        )

        if segments_missing_economics or has_unit_economics_gap:
            missing_names = ", ".join(segments_missing_economics[:3]) if segments_missing_economics else "key segments"
            constraints.append({
                "rule": "segment_economics_missing",
                "severity": "soft",
                "reason": f"Thesis-critical segment economics missing for: {missing_names}",
            })

    def _check_evidence_confidence(
        self, confidence: float | None, risk_assessment: dict, constraints: list
    ) -> None:
        if confidence is not None and confidence < 0.65:
            constraints.append({
                "rule": "low_evidence_confidence",
                "severity": "soft",
                "reason": f"Report evidence confidence ({confidence:.2f}) is below 0.65 threshold",
            })

        risk_score = risk_assessment.get("overall_risk_score")
        if isinstance(risk_score, (int, float)) and risk_score >= 7.0:
            constraints.append({
                "rule": "high_overall_risk",
                "severity": "hard",
                "reason": f"Overall risk score ({risk_score:.1f}) is >= 7.0 — incompatible with Buy",
            })

    def _check_red_flags(
        self, risk_assessment: dict, financial_summary: dict, constraints: list
    ) -> None:
        red_flags = risk_assessment.get("red_flags", [])
        financial_flags = financial_summary.get("financial_red_flags", [])
        all_flags = list(red_flags) + list(financial_flags)

        if len(all_flags) >= 3:
            constraints.append({
                "rule": "multiple_red_flags",
                "severity": "soft",
                "reason": f"{len(all_flags)} red flags present — Buy requires explicit explanation of why they're manageable",
            })

    def _compute_decision_scores(
        self,
        financial_summary: dict,
        risk_assessment: dict,
        valuation_audit: dict,
        peer_quality: dict,
        confidence: float | None,
    ) -> dict[str, float | None]:
        """Compute component scores for the rating decision."""
        quality_score = self._financial_quality_score(financial_summary)
        growth_score = self._growth_score(financial_summary)
        valuation_score = 1.0 if valuation_audit.get("passed") else max(0.3, 1.0 - valuation_audit.get("critical_issue_count", 0) * 0.2)
        risk_score_raw = risk_assessment.get("overall_risk_score")
        risk_score = round(1.0 - (risk_score_raw / 10.0), 2) if isinstance(risk_score_raw, (int, float)) else None
        evidence_score = round(confidence, 2) if confidence is not None else None

        return {
            "quality_score": quality_score,
            "growth_score": growth_score,
            "valuation_score": round(valuation_score, 2),
            "risk_score": risk_score,
            "evidence_score": evidence_score,
        }

    def _financial_quality_score(self, fs: dict) -> float | None:
        latest = fs.get("latest_metrics", {})
        gm = latest.get("gross_margin")
        nm = latest.get("net_margin")
        roe = latest.get("roe")

        if gm is None and nm is None and roe is None:
            return None

        score = 0.5
        if isinstance(gm, (int, float)):
            score += 0.15 if gm > 0.4 else (0.05 if gm > 0.2 else -0.1)
        if isinstance(nm, (int, float)):
            score += 0.15 if nm > 0.15 else (0.05 if nm > 0.05 else -0.1)
        if isinstance(roe, (int, float)):
            score += 0.2 if roe > 0.15 else (0.1 if roe > 0.08 else -0.05)

        return round(min(1.0, max(0.0, score)), 2)

    def _growth_score(self, fs: dict) -> float | None:
        cagr = fs.get("revenue_cagr")
        if not isinstance(cagr, (int, float)):
            return None
        if cagr > 0.3:
            return 0.9
        if cagr > 0.15:
            return 0.7
        if cagr > 0.08:
            return 0.5
        if cagr > 0:
            return 0.3
        return 0.1
