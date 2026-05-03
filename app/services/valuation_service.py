"""Valuation service for value and growth investing styles."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.stock_price_provider import get_current_price
from app.services.supplemental_valuation_service import SupplementalValuationService

settings = get_settings()
logger = get_logger(__name__)


class ValuationService:
    """Compute scenario-based valuations with configurable assumptions."""

    def __init__(self, supplemental_service: SupplementalValuationService | None = None) -> None:
        self.supplemental_service = supplemental_service or SupplementalValuationService()

    def value_investing_valuation(self, financial_summary: dict, assumptions: dict | None = None) -> dict:
        """Fallback value valuation when legacy value-analysis output is unavailable."""

        assumptions = assumptions or {
            "multiple_bull": 24.0,
            "multiple_base": 18.0,
            "multiple_bear": 12.0,
            "cash_flow_haircut": 0.9,
        }
        fcf = float(financial_summary.get("latest_metrics", {}).get("free_cash_flow") or 0)
        normalized = max(fcf * assumptions["cash_flow_haircut"], 0.0)
        return {
            "bull": normalized * assumptions["multiple_bull"],
            "base": normalized * assumptions["multiple_base"],
            "bear": normalized * assumptions["multiple_bear"],
            "assumptions": assumptions,
        }

    def growth_stock_valuation(
        self,
        financial_summary: dict,
        peer_comparison: dict,
        company_profile: dict | None = None,
        assumptions: dict | None = None,
    ) -> dict:
        """Estimate valuation from revenue growth and exit multiple logic."""

        assumptions = assumptions or {
            "sales_multiple_bull": 16.0,
            "sales_multiple_base": 12.0,
            "sales_multiple_bear": 8.0,
            "growth_boost": 1.0,
        }
        company_profile = company_profile or {}
        revenue = float(financial_summary.get("latest_metrics", {}).get("revenue") or 0)
        growth = float(financial_summary.get("revenue_cagr") or 0)
        margin_quality = float(financial_summary.get("latest_metrics", {}).get("net_margin") or 0)
        peer_gm = peer_comparison.get("metrics", {}).get("gross_margin", {}).get("peer_avg")
        quality_adjustment = 1.05 if peer_gm is not None and (financial_summary.get("latest_metrics", {}).get("gross_margin") or 0) >= peer_gm else 0.95
        business_model_text = " ".join(
            str(company_profile.get(key, "")) for key in ("business_model", "market_space", "competition_landscape")
        ).lower()
        sector_bonus = 1.1 if any(term in business_model_text for term in ("ai", "cloud", "platform", "software")) else 1.0
        growth_scalar = max(0.7, min(1.8, (1 + growth * assumptions["growth_boost"] + margin_quality) * sector_bonus))
        return {
            "bull": revenue * assumptions["sales_multiple_bull"] * growth_scalar * quality_adjustment,
            "base": revenue * assumptions["sales_multiple_base"] * growth_scalar,
            "bear": revenue * assumptions["sales_multiple_bear"] * max(0.65, growth_scalar - 0.2),
            "assumptions": assumptions
            | {"quality_adjustment": quality_adjustment, "growth_scalar": growth_scalar, "sector_bonus": sector_bonus},
        }

    def blended_valuation(self, value_result: dict, growth_result: dict) -> dict:
        """Blend primary value analysis and growth framework into one scenario."""

        return {
            "bull": (value_result["bull"] + growth_result["bull"]) / 2,
            "base": (value_result["base"] + growth_result["base"]) / 2,
            "bear": (value_result["bear"] + growth_result["bear"]) / 2,
        }

    def _to_per_share(self, scenario: dict, shares: float, fx_rate: float) -> dict:
        """Convert total-value scenario to per-share in quote currency."""
        if shares <= 0:
            return {"bull": None, "base": None, "bear": None}
        return {
            "bull": round(scenario["bull"] / shares * fx_rate, 2),
            "base": round(scenario["base"] / shares * fx_rate, 2),
            "bear": round(scenario["bear"] / shares * fx_rate, 2),
        }

    def _integrated_per_share(
        self, model_per_share: dict, supplemental: dict | None
    ) -> dict:
        """Blend model per-share with supplemental EPV per-share (weight 40/60)."""
        if not supplemental or supplemental.get("status") != "ok":
            return model_per_share
        sup_lower = supplemental.get("lower_price_limit")
        sup_upper = supplemental.get("upper_price_limit")
        sup_mid = supplemental.get("midpoint_value")
        if not all(v and v > 0 for v in (sup_lower, sup_upper, sup_mid)):
            return model_per_share
        if not all(model_per_share.get(k) for k in ("bull", "base", "bear")):
            return {"bull": sup_upper, "base": sup_mid, "bear": sup_lower}
        w_model, w_epv = 0.4, 0.6
        return {
            "bull": round(model_per_share["bull"] * w_model + sup_upper * w_epv, 2),
            "base": round(model_per_share["base"] * w_model + sup_mid * w_epv, 2),
            "bear": round(model_per_share["bear"] * w_model + sup_lower * w_epv, 2),
        }

    def run(
        self,
        financial_summary: dict,
        peer_comparison: dict,
        company_profile: dict | None = None,
        ticker: str | None = None,
        company_name: str | None = None,
    ) -> tuple[dict, dict]:
        """Return valuation result and consolidated assumptions."""

        supplemental = None
        if settings.enable_supplemental_valuation and ticker and company_name:
            supplemental = self.supplemental_service.evaluate(ticker, company_name)
        if supplemental and supplemental.get("status") == "ok":
            value_result = {
                "bull": float(supplemental.get("upper_price_limit") or 0),
                "base": float(supplemental.get("midpoint_value") or 0),
                "bear": float(supplemental.get("lower_price_limit") or 0),
                "assumptions": {
                    "method": "run_value_analysis_by_code",
                    "report_period": supplemental.get("report_period"),
                    "quote_currency": supplemental.get("quote_currency"),
                },
            }
        else:
            value_result = self.value_investing_valuation(financial_summary)

        growth_result = self.growth_stock_valuation(financial_summary, peer_comparison, company_profile=company_profile)
        blended_total = self.blended_valuation(value_result, growth_result)

        shares = 0.0
        fx_rate = 1.0
        quote_currency = "CNY"
        if supplemental and supplemental.get("status") == "ok":
            shares = float(supplemental.get("summary", {}).get("total_shares") or 0)
            fx_rate = float(supplemental.get("fx_rate") or 1.0)
            quote_currency = supplemental.get("quote_currency") or "CNY"

        if shares > 0:
            model_per_share = self._to_per_share(blended_total, shares, fx_rate)
            integrated = self._integrated_per_share(model_per_share, supplemental)
        else:
            model_per_share = None
            integrated = blended_total

        has_per_share = shares > 0
        result = {
            "value_investing": {k: value_result[k] for k in ("bull", "base", "bear")},
            "primary_value": {k: value_result[k] for k in ("bull", "base", "bear")},
            "growth": {k: growth_result[k] for k in ("bull", "base", "bear")},
            "blended_total_value": blended_total,
            "blended": integrated,
            "per_share_currency": quote_currency if has_per_share else "Reporting currency (market cap)",
            "shares_outstanding": shares if has_per_share else None,
            "fx_rate": fx_rate if has_per_share else None,
            "is_per_share": has_per_share,
            "commentary": [
                "Primary valuation method prioritizes value analysis results from run_value_analysis_by_code.",
                "Growth stock valuation serves as a supplement, adjusted for revenue scale, growth rate, and earnings quality.",
                "Blended valuation is the average of primary and growth valuations." if not has_per_share else "Blended valuation is output on a per-share basis; primary valuation takes priority over growth valuation.",
            ],
        }
        if supplemental:
            result["legacy_value_analysis"] = supplemental

        price_info = get_current_price(ticker) if ticker else {}
        current_price = price_info.get("price")
        if current_price and current_price > 0:
            result["current_price"] = current_price
            result["price_source"] = price_info.get("source", "unknown")
            if price_info.get("pe_ratio"):
                result["pe_ratio"] = price_info["pe_ratio"]
            if price_info.get("pb_ratio"):
                result["pb_ratio"] = price_info["pb_ratio"]
            if price_info.get("dividend_yield"):
                result["dividend_yield"] = price_info["dividend_yield"]

            blended = integrated
            bull = blended.get("bull")
            base = blended.get("base")
            bear = blended.get("bear")
            signal = self._price_signal(current_price, bull, base, bear)
            result["price_vs_valuation"] = signal
            result["commentary"].append(signal["commentary"])
            logger.info("price_vs_valuation", ticker=ticker, price=current_price, signal=signal["signal"], upside=signal.get("upside_pct"))

        assumptions = {
            "value_investing": value_result["assumptions"],
            "growth": growth_result["assumptions"],
        }
        if supplemental:
            assumptions["legacy_value_analysis"] = {
                "script": supplemental.get("script"),
                "status": supplemental.get("status"),
                "report_period": supplemental.get("report_period"),
                "quote_currency": supplemental.get("quote_currency"),
                "fx_rate": fx_rate,
                "shares_outstanding": shares if shares > 0 else None,
                "blend_weights": "model 40% + EPV 60%" if supplemental.get("status") == "ok" else None,
            }
        return result, assumptions

    def _price_signal(self, current_price: float, bull: float | None, base: float | None, bear: float | None) -> dict:
        """Compare current price to valuation range and return buy/watch/avoid signal."""

        if not base or base <= 0:
            return {"signal": "insufficient_data", "commentary": "Valuation data insufficient for price comparison."}

        upside = (base - current_price) / current_price
        margin_of_safety = (current_price - (bear or 0)) / current_price if bear and bear > 0 else None

        if current_price <= (bear or 0):
            signal = "strong_buy"
            commentary = f"Current price ({current_price:.2f}) is below bear-case valuation ({bear:.2f}), suggesting deep value with {upside:+.1%} upside to base case."
        elif upside >= 0.25:
            signal = "buy"
            commentary = f"Current price ({current_price:.2f}) is {upside:+.1%} below base-case valuation ({base:.2f}), offering meaningful upside."
        elif upside >= 0.05:
            signal = "watch"
            commentary = f"Current price ({current_price:.2f}) is {upside:+.1%} vs base-case ({base:.2f}), moderately undervalued — watch for a better entry."
        elif upside >= -0.10:
            signal = "hold"
            commentary = f"Current price ({current_price:.2f}) is near fair value (base: {base:.2f}), {upside:+.1%} upside."
        elif bull and current_price >= bull:
            signal = "avoid"
            commentary = f"Current price ({current_price:.2f}) exceeds bull-case valuation ({bull:.2f}), market appears to price in optimistic assumptions."
        else:
            signal = "overvalued"
            commentary = f"Current price ({current_price:.2f}) is {upside:+.1%} vs base-case ({base:.2f}), suggesting overvaluation."

        return {
            "signal": signal,
            "current_price": current_price,
            "base_value": base,
            "upside_pct": round(upside * 100, 1),
            "margin_of_safety_pct": round(margin_of_safety * 100, 1) if margin_of_safety is not None else None,
            "commentary": commentary,
        }
