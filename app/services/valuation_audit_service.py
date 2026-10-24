"""Valuation audit service — sanity-check valuation assumptions before final rating."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class ValuationAuditService:
    """Deterministic audit of valuation assumptions and internal consistency.

    Returns an audit dict with pass/fail flags and a list of issues.
    If audit fails, callers should cap the final rating at "watch".
    """

    def audit(
        self,
        valuation_result: dict[str, Any],
        valuation_assumptions: dict[str, Any],
        financial_summary: dict[str, Any],
    ) -> dict[str, Any]:
        issues: list[str] = []
        checks: dict[str, bool] = {}

        self._check_currency(valuation_result, checks, issues)
        self._check_share_basis(valuation_result, checks, issues)
        self._check_share_count(valuation_result, financial_summary, checks, issues)
        self._check_fcf_consistency(valuation_result, financial_summary, checks, issues)
        self._check_discount_rate(valuation_assumptions, checks, issues)
        self._check_terminal_growth(valuation_assumptions, checks, issues)
        self._check_margin_assumptions(valuation_assumptions, financial_summary, checks, issues)
        self._check_net_cash(valuation_result, financial_summary, checks, issues)
        self._check_scenario_consistency(valuation_result, checks, issues)
        self._check_extreme_upside(valuation_result, checks, issues)

        passed = all(checks.values()) if checks else False
        audit_result = {
            "passed": passed,
            "checks": checks,
            "issues": issues,
            "issue_count": len(issues),
            "critical_issue_count": sum(1 for k, v in checks.items() if not v),
        }

        if not passed:
            logger.warning(
                "valuation_audit_failed",
                issue_count=len(issues),
                critical_count=audit_result["critical_issue_count"],
            )

        return audit_result

    def _check_currency(
        self, valuation: dict, checks: dict, issues: list
    ) -> None:
        ccy = valuation.get("per_share_currency", "")
        has_price = valuation.get("current_price") is not None
        if has_price and not ccy:
            checks["currency_stated"] = False
            issues.append("Per-share currency not stated despite having current price")
        elif has_price and "reporting" in str(ccy).lower():
            checks["currency_stated"] = False
            issues.append(f"Currency is ambiguous: '{ccy}' — should be explicit (USD, HKD, CNY)")
        else:
            checks["currency_stated"] = True

    def _check_share_basis(
        self, valuation: dict, checks: dict, issues: list
    ) -> None:
        """Check per-share vs per-ADS basis is explicit."""
        has_price = valuation.get("current_price") is not None
        blended = valuation.get("blended", {})
        has_per_share = blended.get("base") is not None

        if has_price and has_per_share:
            commentary = " ".join(str(c) for c in valuation.get("commentary", []))
            mentions_ads = "ads" in commentary.lower() or "adr" in commentary.lower()
            mentions_share = "per share" in commentary.lower() or "per-share" in commentary.lower()
            if not mentions_ads and not mentions_share:
                checks["share_basis_explicit"] = False
                issues.append("Valuation does not clarify per-share vs per-ADS basis")
            else:
                checks["share_basis_explicit"] = True
        else:
            checks["share_basis_explicit"] = True

    def _check_share_count(
        self, valuation: dict, financial_summary: dict, checks: dict, issues: list
    ) -> None:
        shares = valuation.get("shares_outstanding")
        if shares is not None and shares > 0:
            revenue = financial_summary.get("latest_metrics", {}).get("revenue")
            if revenue and revenue > 0:
                rev_per_share = revenue / shares
                if rev_per_share < 0.001 or rev_per_share > 1_000_000:
                    checks["share_count_sane"] = False
                    issues.append(
                        f"Revenue/share ({rev_per_share:,.4f}) is extreme — "
                        f"share count ({shares:,.0f}) may be wrong or in different units"
                    )
                else:
                    checks["share_count_sane"] = True
            else:
                checks["share_count_sane"] = True
        else:
            has_price = valuation.get("current_price") is not None
            if has_price:
                checks["share_count_sane"] = False
                issues.append("Current price available but shares_outstanding is missing — cannot validate per-share valuation")
            else:
                checks["share_count_sane"] = True

    def _check_fcf_consistency(
        self, valuation: dict, financial_summary: dict, checks: dict, issues: list
    ) -> None:
        """Check that valuation FCF and financial summary FCF are consistent in units."""
        latest = financial_summary.get("latest_metrics", {})
        fcf = latest.get("free_cash_flow")
        net_income = latest.get("net_income")
        revenue = latest.get("revenue")

        if fcf is not None and revenue is not None and revenue > 0:
            fcf_margin = abs(fcf) / revenue
            if fcf_margin > 2.0:
                checks["fcf_unit_consistent"] = False
                issues.append(
                    f"FCF/revenue ratio is {fcf_margin:.1f}x — possible unit mismatch "
                    f"(FCF={fcf:,.0f}, revenue={revenue:,.0f})"
                )
            else:
                checks["fcf_unit_consistent"] = True
        else:
            checks["fcf_unit_consistent"] = True

        if fcf is not None and net_income is not None and net_income != 0:
            ratio = fcf / net_income
            if ratio > 5.0 or ratio < -5.0:
                issues.append(
                    f"FCF/net_income ratio is {ratio:.1f}x — "
                    f"unusually large, may indicate different units or extraordinary items"
                )

    def _check_discount_rate(
        self, assumptions: dict, checks: dict, issues: list
    ) -> None:
        vi_assumptions = assumptions.get("value_investing", {})
        growth_assumptions = assumptions.get("growth", {})

        has_any_discount = False
        for key in ("discount_rate", "wacc", "required_return"):
            if vi_assumptions.get(key) or growth_assumptions.get(key):
                has_any_discount = True
                rate = vi_assumptions.get(key) or growth_assumptions.get(key)
                if isinstance(rate, (int, float)) and (rate < 0.03 or rate > 0.25):
                    checks["discount_rate_reasonable"] = False
                    issues.append(f"Discount rate {rate:.1%} is outside reasonable range (3%-25%)")
                    return

        if not has_any_discount:
            if "multiple_base" in vi_assumptions or "sales_multiple_base" in growth_assumptions:
                checks["discount_rate_reasonable"] = True
            else:
                checks["discount_rate_reasonable"] = False
                issues.append("No discount rate or valuation multiples found in assumptions")
                return

        checks["discount_rate_reasonable"] = True

    def _check_terminal_growth(
        self, assumptions: dict, checks: dict, issues: list
    ) -> None:
        for section in (assumptions.get("value_investing", {}), assumptions.get("growth", {})):
            tg = section.get("terminal_growth") or section.get("terminal_growth_rate")
            if isinstance(tg, (int, float)):
                if tg > 0.06:
                    checks["terminal_growth_reasonable"] = False
                    issues.append(f"Terminal growth rate {tg:.1%} exceeds 6% — likely too aggressive")
                    return
                if tg < 0:
                    checks["terminal_growth_reasonable"] = False
                    issues.append(f"Terminal growth rate {tg:.1%} is negative")
                    return
        checks["terminal_growth_reasonable"] = True

    def _check_margin_assumptions(
        self, assumptions: dict, financial_summary: dict, checks: dict, issues: list
    ) -> None:
        latest = financial_summary.get("latest_metrics", {})
        current_net_margin = latest.get("net_margin")
        current_gross_margin = latest.get("gross_margin")

        for section in (assumptions.get("value_investing", {}), assumptions.get("growth", {})):
            assumed_margin = section.get("normalized_margin") or section.get("target_margin")
            if isinstance(assumed_margin, (int, float)) and isinstance(current_net_margin, (int, float)):
                if assumed_margin > current_net_margin * 2.0 and assumed_margin > 0.10:
                    checks["margin_assumptions_reasonable"] = False
                    issues.append(
                        f"Assumed margin ({assumed_margin:.1%}) is >2x current "
                        f"({current_net_margin:.1%}) — may be overly optimistic"
                    )
                    return

        checks["margin_assumptions_reasonable"] = True

    def _check_net_cash(
        self, valuation: dict, financial_summary: dict, checks: dict, issues: list
    ) -> None:
        """Flag if large net cash exists but isn't reflected in valuation."""
        latest = financial_summary.get("latest_metrics", {})
        cash = latest.get("cash_and_equivalents") or latest.get("net_cash")
        revenue = latest.get("revenue")

        if isinstance(cash, (int, float)) and isinstance(revenue, (int, float)) and revenue > 0:
            if cash / revenue > 0.5:
                blended = valuation.get("blended_total_value", {})
                base_val = blended.get("base", 0)
                if base_val > 0 and cash / base_val > 0.3:
                    issues.append(
                        f"Net cash ({cash:,.0f}) is {cash/base_val:.0%} of base valuation — "
                        f"verify net cash adjustment is included"
                    )

        checks["net_cash_noted"] = True

    def _check_scenario_consistency(
        self, valuation: dict, checks: dict, issues: list
    ) -> None:
        """Bear < base < bull for all valuation methods."""
        for method in ("value_investing", "growth", "blended"):
            scenario = valuation.get(method, {})
            bear = scenario.get("bear")
            base = scenario.get("base")
            bull = scenario.get("bull")

            if bear is not None and base is not None and bull is not None:
                if not (bear <= base <= bull):
                    checks["scenario_ordering"] = False
                    issues.append(
                        f"Scenario ordering violated in {method}: "
                        f"bear={bear:,.2f}, base={base:,.2f}, bull={bull:,.2f}"
                    )
                    return

                if base > 0 and bull > 0:
                    spread = (bull - bear) / base
                    if spread > 5.0:
                        issues.append(
                            f"Scenario spread in {method} is {spread:.1f}x base — "
                            f"unusually wide, may indicate low conviction"
                        )

        checks.setdefault("scenario_ordering", True)

    def _check_extreme_upside(
        self, valuation: dict, checks: dict, issues: list
    ) -> None:
        """If price is below bear by >30%, require explicit audit confirmation."""
        pvv = valuation.get("price_vs_valuation", {})
        current_price = pvv.get("current_price") or valuation.get("current_price")
        blended = valuation.get("blended", {})
        bear = blended.get("bear")

        if current_price and bear and bear > 0 and current_price > 0:
            if current_price < bear * 0.70:
                checks["extreme_upside_audited"] = False
                issues.append(
                    f"Current price ({current_price:,.2f}) is >{30}% below bear-case "
                    f"({bear:,.2f}) — extreme upside claim requires explicit audit. "
                    f"Verify: currency, ADS ratio, share count, FCF normalization."
                )
                return

        checks["extreme_upside_audited"] = True
