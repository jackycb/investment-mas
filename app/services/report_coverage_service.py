"""Post-report quality gate: check evidence sufficiency across all sections."""

from __future__ import annotations

from typing import Any


class ReportCoverageService:
    """Validate the final report for evidence gaps, shallow analysis, and missing sections."""

    def check(self, state: dict[str, Any]) -> dict[str, Any]:
        warnings: list[str] = []
        section_scores: dict[str, dict[str, Any]] = {}

        section_scores["business_analysis"] = self._check_business(state)
        section_scores["segment_analysis"] = self._check_segment(state)
        section_scores["culture_analysis"] = self._check_culture(state)
        section_scores["financial_analysis"] = self._check_financial(state)
        section_scores["valuation_analysis"] = self._check_valuation(state)
        section_scores["risk_analysis"] = self._check_risk(state)
        section_scores["peer_analysis"] = self._check_peers(state)

        for section, result in section_scores.items():
            for w in result.get("issues", []):
                warnings.append(f"[{section}] {w}")

        passed = all(r.get("passed", False) for r in section_scores.values())
        return {
            "passed": passed,
            "section_scores": section_scores,
            "warnings": warnings,
        }

    def _check_business(self, state: dict[str, Any]) -> dict[str, Any]:
        ba = state.get("business_analysis", {})
        issues: list[str] = []
        if not ba:
            issues.append("Business analysis result is empty.")
        elif not ba.get("core_judgement") or ba.get("core_judgement") == "Insufficient information":
            issues.append("Business analysis core judgement is empty or insufficient.")
        return {"passed": len(issues) == 0, "issues": issues}

    def _check_segment(self, state: dict[str, Any]) -> dict[str, Any]:
        sa = state.get("segment_analysis", {})
        issues: list[str] = []
        if isinstance(sa, dict) and sa.get("is_group_company") and not sa.get("segments"):
            issues.append("Identified as a group company but no business segment breakdown was produced.")
        return {"passed": len(issues) == 0, "issues": issues}

    def _check_culture(self, state: dict[str, Any]) -> dict[str, Any]:
        ca = state.get("culture_analysis", {})
        issues: list[str] = []
        if not ca or ca.get("core_judgement") == "Insufficient information":
            issues.append("Corporate culture / governance analysis core judgement is insufficient.")
        if not ca.get("management_members"):
            issues.append("No management member information was produced.")
        return {"passed": len(issues) == 0, "issues": issues}

    def _check_financial(self, state: dict[str, Any]) -> dict[str, Any]:
        fs = state.get("financial_summary", {})
        issues: list[str] = []
        if not fs.get("years"):
            issues.append("No historical financial data available.")
        return {"passed": len(issues) == 0, "issues": issues}

    def _check_valuation(self, state: dict[str, Any]) -> dict[str, Any]:
        vr = state.get("valuation_result", {})
        issues: list[str] = []
        blended = vr.get("blended", {})
        if not blended.get("base"):
            issues.append("Blended valuation base is empty.")
        return {"passed": len(issues) == 0, "issues": issues}

    def _check_risk(self, state: dict[str, Any]) -> dict[str, Any]:
        ra = state.get("risk_assessment", {})
        issues: list[str] = []
        if not ra.get("key_risks"):
            issues.append("No key risks identified.")
        if not ra.get("monitoring_signals"):
            issues.append("No monitoring signals produced.")
        return {"passed": len(issues) == 0, "issues": issues}

    def _check_peers(self, state: dict[str, Any]) -> dict[str, Any]:
        pa = state.get("peer_analysis", {})
        issues: list[str] = []
        if not pa.get("core_peers") and not pa.get("peer_details"):
            issues.append("No core competitors identified.")
        return {"passed": len(issues) == 0, "issues": issues}
