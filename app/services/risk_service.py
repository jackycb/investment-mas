"""Rule-first risk scoring service."""

from __future__ import annotations

from typing import Any

from app.services.retrieval_service import RetrievalService


class RiskService:
    """Evaluate research risks from rules and explain the evidence."""

    event_risk_types = {"guidance_cut", "lawsuit", "management_change", "regulatory_action", "margin_pressure", "demand_slowdown"}

    def __init__(self, retrieval_service: RetrievalService | None = None) -> None:
        self.retrieval_service = retrieval_service

    def assess(
        self,
        financial_summary: dict,
        peer_comparison: dict,
        red_flags: list[str],
        event_list: list[dict],
        valuation_result: dict,
        retrieval_context: dict[str, Any] | None = None,
    ) -> dict:
        """Return explainable risk assessment output."""

        fundamentals = 2.0
        competition = 2.0
        governance = 1.5
        event_risk = 1.5
        valuation_risk = 2.0
        evidence: list[dict[str, str | float]] = []
        monitoring_signals: list[str] = []
        key_risks: list[str] = []
        red_flag_output = list(red_flags)

        revenues = financial_summary.get("years", [])
        rev_cagr = financial_summary.get("revenue_cagr")
        if rev_cagr is not None and rev_cagr < 0.08:
            fundamentals += 2.0
            evidence.append({"dimension": "fundamentals", "rule": "low_revenue_cagr", "score": 2.0})
            key_risks.append("Revenue compounding growth is decelerating.")

        gross_margin = [x.get("value") for x in financial_summary.get("gross_margin_trend", []) if x.get("value") is not None]
        if len(gross_margin) >= 3 and gross_margin[-1] < gross_margin[-2] < gross_margin[-3]:
            fundamentals += 1.5
            evidence.append({"dimension": "fundamentals", "rule": "declining_gross_margin", "score": 1.5})
            monitoring_signals.append("Monitor whether quarterly gross margin stabilizes.")

        ocf_ratios = [x.get("ratio") for x in financial_summary.get("ocf_vs_net_income", []) if x.get("ratio") is not None]
        if ocf_ratios and sum(r < 1.0 for r in ocf_ratios) >= 3:
            fundamentals += 1.5
            evidence.append({"dimension": "fundamentals", "rule": "weak_cash_conversion", "score": 1.5})
            key_risks.append("Cash conversion consistently lags reported earnings.")

        for metric in ("gross_margin", "net_margin", "roe"):
            data = peer_comparison.get("metrics", {}).get(metric, {})
            company_value = data.get("company_value")
            peer_avg = data.get("peer_avg")
            if company_value is not None and peer_avg is not None and company_value + 0.03 < peer_avg:
                competition += 1.0
                evidence.append({"dimension": "competition", "rule": f"{metric}_below_peers", "score": 1.0})

        for event in event_list:
            if event.get("event_type") in self.event_risk_types:
                event_risk += float(event.get("importance_score") or 0.5) * 2
                evidence.append(
                    {
                        "dimension": "event",
                        "rule": str(event.get("event_type")),
                        "score": float(event.get("importance_score") or 0.5) * 2,
                    }
                )
                key_risks.append(str(event.get("event_summary")))
        if any(event.get("event_type") in {"management_change", "regulatory_action", "lawsuit"} for event in event_list):
            governance += 1.5
            monitoring_signals.append("Continue tracking governance, litigation, and management changes.")

        retrieval_evidence = self._retrieve_risk_evidence(retrieval_context or {})
        if retrieval_evidence:
            evidence.extend(retrieval_evidence)
            if any(item.get("rule") == "document_risk_factor" for item in retrieval_evidence):
                fundamentals += 0.8
                key_risks.append("Risk factor disclosures in filings require ongoing monitoring.")
            if any(item.get("rule") == "document_management_signal" for item in retrieval_evidence):
                governance += 0.5
                monitoring_signals.append("Track changes in management commentary across filings.")

        growth_base = valuation_result.get("growth", {}).get("base")
        blended_bull = valuation_result.get("blended", {}).get("bull")
        blended_base = valuation_result.get("blended", {}).get("base")
        if growth_base and blended_bull and growth_base > blended_bull * 0.95:
            valuation_risk += 2.0
            evidence.append({"dimension": "valuation", "rule": "high_growth_expectation", "score": 2.0})
            red_flag_output.append("Current growth valuation already prices in an optimistic scenario.")
        elif blended_base and blended_base > 0:
            valuation_risk += 0.5

        overall = min(10.0, round((fundamentals + competition + governance + event_risk + valuation_risk) / 5, 2))

        price_signal = valuation_result.get("price_vs_valuation", {}).get("signal", "")
        current_price = valuation_result.get("current_price")
        upside_pct = valuation_result.get("price_vs_valuation", {}).get("upside_pct")

        if not revenues:
            recommendation = "research_more"
        elif price_signal in ("strong_buy", "buy") and overall <= 5.0:
            recommendation = "buy"
        elif price_signal in ("avoid", "overvalued") and overall >= 4.0:
            recommendation = "avoid"
        elif price_signal in ("strong_buy", "buy"):
            recommendation = "watch"
        elif overall <= 3.0:
            recommendation = "buy"
        elif overall >= 7.0:
            recommendation = "avoid"
        else:
            recommendation = "watch"

        if current_price and upside_pct is not None:
            base_val = valuation_result.get("price_vs_valuation", {}).get("base_value")
            if base_val:
                monitoring_signals.append(f"Current price {current_price:.2f} vs base valuation {base_val:.2f} ({upside_pct:+.1f}% {'upside' if upside_pct > 0 else 'downside'}).")

        return {
            "fundamentals_risk_score": min(10.0, round(fundamentals, 2)),
            "competition_risk_score": min(10.0, round(competition, 2)),
            "governance_risk_score": min(10.0, round(governance, 2)),
            "event_risk_score": min(10.0, round(event_risk, 2)),
            "valuation_risk_score": min(10.0, round(valuation_risk, 2)),
            "overall_risk_score": overall,
            "red_flags": red_flag_output,
            "key_risks": list(dict.fromkeys(key_risks))[:8],
            "monitoring_signals": list(dict.fromkeys(monitoring_signals + ["Continue monitoring margin trends relative to peers."]))[:8],
            "recommendation": recommendation,
            "supporting_evidence": evidence,
        }

    def _retrieve_risk_evidence(self, retrieval_context: dict[str, Any]) -> list[dict[str, Any]]:
        """Optionally retrieve filing evidence to support risk explanations."""

        company_id = retrieval_context.get("company_id")
        if not company_id:
            return []
        try:
            if self.retrieval_service is None:
                self.retrieval_service = RetrievalService()

            evidence: list[dict[str, Any]] = []
            risk_hits = self.retrieval_service.search(
                query="Retrieve risk factors, regulatory risks, litigation, operational uncertainties, demand slowdown, margin pressure.",
                filters={"company_id": company_id, "filing_type": "annual_report"},
                top_k=3,
            )
            for item in risk_hits:
                evidence.append(
                    {
                        "dimension": "fundamentals",
                        "rule": "document_risk_factor",
                        "score": round(float(item.get("score") or 0.0), 4),
                        "section": item.get("section"),
                        "filing_id": item.get("filing_id"),
                    }
                )

            management_hits = self.retrieval_service.search(
                query="Retrieve management, governance, board of directors, compliance, key management discussion.",
                filters={"company_id": company_id, "filing_type": "annual_report"},
                top_k=2,
            )
            for item in management_hits:
                evidence.append(
                    {
                        "dimension": "governance",
                        "rule": "document_management_signal",
                        "score": round(float(item.get("score") or 0.0), 4),
                        "section": item.get("section"),
                        "filing_id": item.get("filing_id"),
                    }
                )
            return evidence
        except Exception:
            return []
