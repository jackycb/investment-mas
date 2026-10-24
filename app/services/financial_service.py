"""Financial analytics service."""

from __future__ import annotations

from statistics import mean, median

from app.providers.financial_data_provider import FinancialDataProvider
from app.core.config import get_settings
from app.services.legacy_financial_metric_service import LegacyFinancialMetricService

settings = get_settings()


def _calc_cagr(start_value: float, end_value: float, periods: int) -> float | None:
    if start_value <= 0 or end_value <= 0 or periods <= 0:
        return None
    return (end_value / start_value) ** (1 / periods) - 1


class FinancialService:
    """Analyze financial history and peer comparisons."""

    def __init__(
        self,
        provider: FinancialDataProvider,
        legacy_metric_service: LegacyFinancialMetricService | None = None,
    ) -> None:
        self.provider = provider
        self.legacy_metric_service = legacy_metric_service or LegacyFinancialMetricService()

    def summarize_company(self, ticker: str, company_name: str) -> tuple[dict, list[dict], list[str]]:
        """Return summarized financials, raw annual reports, and warnings."""

        reports = list(reversed(self.provider.load_company_financials(ticker, company_name, years=5)))
        if not reports:
            warning = "Could not obtain usable annual report data from local directory or database fallback sources."
            return {
                "years": [],
                "revenue_cagr": None,
                "gross_margin_trend": [],
                "net_margin_trend": [],
                "roe_trend": [],
                "ocf_vs_net_income": [],
                "latest_metrics": {},
                "narrative": ["Could not obtain annual report data for the last 5 years; financial analysis returns empty results."],
                "warnings": [warning],
                "data_source": "unavailable",
                "data_source_label": "unavailable",
                "provider_name": self.provider.__class__.__name__,
                "source_detail": {},
            }, [], [warning]

        reports = self._enrich_missing_metrics(ticker, reports)
        reports = self._derive_missing_financial_values(reports)
        years = [row["fiscal_year"] for row in reports]
        revenue_cagr = _calc_cagr(reports[0]["revenue"], reports[-1]["revenue"], len(reports) - 1)
        gross_margin_trend = [{"fiscal_year": row["fiscal_year"], "value": row.get("gross_margin")} for row in reports]
        net_margin_trend = [{"fiscal_year": row["fiscal_year"], "value": row.get("net_margin")} for row in reports]
        roe_trend = [{"fiscal_year": row["fiscal_year"], "value": row.get("roe")} for row in reports]
        roic_trend = [{"fiscal_year": row["fiscal_year"], "value": row.get("roic")} for row in reports]
        ocf_vs_net_income = [
            {
                "fiscal_year": row["fiscal_year"],
                "operating_cash_flow": row.get("operating_cash_flow"),
                "net_income": row.get("net_income"),
                "ratio": (row["operating_cash_flow"] / row["net_income"]) if row.get("operating_cash_flow") and row.get("net_income") else None,
            }
            for row in reports
        ]
        latest = reports[-1]
        latest_metrics, latest_metric_years = self._build_latest_metrics(reports)
        raw_source = latest.get("raw_source") or {}
        source_kind = raw_source.get("source_kind", "provider")
        data_source_label = {
            "local_directory": "Local financial report directory",
            "database": "Database",
            "provider": "Provider",
            "unavailable": "Unavailable",
        }.get(source_kind, str(source_kind))
        narrative = [
            f"Revenue CAGR over the last {len(reports)} years is {revenue_cagr:.2%}." if revenue_cagr is not None else "Unable to reliably calculate revenue CAGR at this time.",
            f"Latest gross margin is {latest_metrics['gross_margin']:.2%}; net margin is {latest_metrics['net_margin']:.2%}."
            if latest_metrics.get("gross_margin") is not None and latest_metrics.get("net_margin") is not None
            else "Margin data has gaps; conclusions should be interpreted with caution.",
            f"Latest available ROE is {latest_metrics['roe']:.2%}; ROIC is {latest_metrics['roic']:.2%}."
            if latest_metrics.get("roe") is not None and latest_metrics.get("roic") is not None
            else "ROE/ROIC data has gaps; return-on-capital analysis should be interpreted with caution.",
        ]
        warnings: list[str] = []
        unavailable_fields = [
            field
            for field in ("revenue", "gross_margin", "net_margin", "roe", "roic", "operating_cash_flow", "net_income")
            if all(row.get(field) is None for row in reports)
        ]
        if unavailable_fields:
            warnings.append(f"The following financial fields are unavailable: {', '.join(sorted(set(unavailable_fields)))}.")
        stale_fields = [
            field
            for field in ("net_margin", "roe", "roic", "net_income", "operating_cash_flow")
            if latest.get(field) is None and latest_metrics.get(field) is not None
        ]
        if stale_fields:
            stale_parts = [f"{field} as of {latest_metric_years.get(field)}" for field in stale_fields]
            warnings.append(f"Latest fiscal year has gaps; using latest available metrics for: {', '.join(stale_parts)}.")

        return {
            "years": years,
            "revenue_cagr": revenue_cagr,
            "gross_margin_trend": gross_margin_trend,
            "net_margin_trend": net_margin_trend,
            "roe_trend": roe_trend,
            "roic_trend": roic_trend,
            "ocf_vs_net_income": ocf_vs_net_income,
            "latest_metrics": {
                "revenue": latest_metrics.get("revenue"),
                "gross_margin": latest_metrics.get("gross_margin"),
                "net_margin": latest_metrics.get("net_margin"),
                "roe": latest_metrics.get("roe"),
                "roic": latest_metrics.get("roic"),
                "net_income": latest_metrics.get("net_income"),
                "operating_cash_flow": latest_metrics.get("operating_cash_flow"),
                "free_cash_flow": latest_metrics.get("free_cash_flow"),
                "shares_outstanding": latest_metrics.get("shares_outstanding"),
            },
            "latest_metric_years": latest_metric_years,
            "narrative": narrative,
            "warnings": warnings,
            "data_source": source_kind,
            "data_source_label": data_source_label,
            "provider_name": self.provider.__class__.__name__,
            "source_detail": raw_source,
        }, reports, warnings

    def compare_peers(self, ticker: str, company_name: str, company_reports: list[dict], peer_list: list[dict]) -> dict:
        """Compare key metrics against peers using Yahoo Finance for consistency.

        All data (company + peers) comes from Yahoo Finance TTM figures
        so that the comparison uses a single consistent data source.
        """
        from app.providers.financial_data_provider import fetch_yahoo_peer_financials

        all_tickers = [ticker] + [peer["ticker"] for peer in peer_list]
        yahoo_data = fetch_yahoo_peer_financials(all_tickers)

        company_yahoo = (yahoo_data.get(ticker) or [None])[0]
        if not company_yahoo:
            if not company_reports:
                return {"metrics": {}, "peer_details": [], "summary": ["No financial data available for peer comparison."]}
            company_yahoo = company_reports[-1]
            company_yahoo["raw_source"] = {"source_kind": "local_annual_report", "note": "Yahoo Finance unavailable, fell back to local data"}

        peer_details: list[dict] = []
        metric_values = {"gross_margin": [], "net_margin": [], "roe": [], "roic": []}
        for peer in peer_list:
            peer_records = yahoo_data.get(peer["ticker"], [])
            if not peer_records:
                continue
            latest = peer_records[0]
            peer_entry = {"ticker": peer["ticker"], "company_name": peer["company_name"], "metrics": latest}
            peer_details.append(peer_entry)
            for metric in metric_values:
                if latest.get(metric) is not None:
                    metric_values[metric].append(latest[metric])

        metrics: dict[str, dict] = {}
        for metric, values in metric_values.items():
            company_value = company_yahoo.get(metric)
            if values:
                sorted_values = sorted(values + ([company_value] if company_value is not None else []), reverse=True)
                rank = sorted_values.index(company_value) + 1 if company_value is not None else None
                metrics[metric] = {
                    "company_value": company_value,
                    "peer_avg": mean(values),
                    "peer_median": median(values),
                    "ranking": rank,
                    "peer_count": len(values),
                    "data_source": "yahoo_finance_ttm",
                }
            else:
                metrics[metric] = {
                    "company_value": company_value,
                    "peer_avg": None,
                    "peer_median": None,
                    "ranking": None,
                    "peer_count": 0,
                    "data_source": "yahoo_finance_ttm",
                }

        summary = []
        for metric, data in metrics.items():
            if data["peer_avg"] is None or data["company_value"] is None:
                continue
            relation = "above" if data["company_value"] >= data["peer_avg"] else "below"
            metric_label = {"gross_margin": "Gross margin", "net_margin": "Net margin", "roe": "ROE", "roic": "ROIC"}.get(metric, metric)
            summary.append(f"{metric_label} is {relation} the peer average.")
        return {"metrics": metrics, "peer_details": peer_details, "summary": summary, "data_source": "yahoo_finance_ttm"}

    def identify_red_flags(self, financial_summary: dict, peer_comparison: dict) -> list[str]:
        """Return explainable financial red flags."""

        red_flags: list[str] = []
        revenue_cagr = financial_summary.get("revenue_cagr")
        if revenue_cagr is not None and revenue_cagr < 0.05:
            red_flags.append("Revenue CAGR over the last 5 years is below 5%.")

        gm = [point.get("value") for point in financial_summary.get("gross_margin_trend", []) if point.get("value") is not None]
        if len(gm) >= 3 and gm[-1] < gm[0] - 0.05:
            red_flags.append("Gross margin declined significantly during the observation period.")

        ocf_checks = [
            item for item in financial_summary.get("ocf_vs_net_income", [])
            if item.get("ratio") is not None and item.get("ratio", 0) < 1.0
        ]
        if len(ocf_checks) >= 3:
            red_flags.append("Operating cash flow was persistently weaker than net income across multiple years.")

        for metric in ("gross_margin", "net_margin", "roe", "roic"):
            metric_data = peer_comparison.get("metrics", {}).get(metric, {})
            if metric_data.get("peer_avg") is not None and metric_data.get("company_value") is not None:
                if metric_data["company_value"] + 0.03 < metric_data["peer_avg"]:
                    metric_label = {"gross_margin": "Gross margin", "net_margin": "Net margin", "roe": "ROE", "roic": "ROIC"}.get(metric, metric)
                    red_flags.append(f"{metric_label} is significantly below the peer average.")
        return red_flags

    def _enrich_missing_metrics(self, ticker: str, reports: list[dict]) -> list[dict]:
        """Fill missing margin/return metrics with legacy Futu calculations when needed."""

        if not reports or not settings.enable_legacy_financial_metric_enrichment:
            return reports

        needs_enrichment = any(
            row.get(field) is None
            for row in reports
            for field in ("gross_margin", "net_margin", "roe", "roic")
        )
        if not needs_enrichment:
            return reports

        payload = self.legacy_metric_service.compute(ticker)
        if not payload:
            return reports

        annual_metrics = {
            int(item.get("fiscal_year")): item
            for item in payload.get("annual_metrics", [])
            if isinstance(item, dict) and item.get("fiscal_year")
        }
        enriched: list[dict] = []
        for row in reports:
            item = annual_metrics.get(int(row.get("fiscal_year", 0)))
            if not item:
                enriched.append(row)
                continue
            enriched_row = dict(row)
            for field in ("gross_margin", "net_margin", "roe", "roa", "roic"):
                if enriched_row.get(field) is None and item.get(field) is not None:
                    enriched_row[field] = item.get(field)
            raw_source = dict(enriched_row.get("raw_source") or {})
            raw_source["legacy_metric_enrichment"] = payload.get("source")
            enriched_row["raw_source"] = raw_source
            enriched.append(enriched_row)
        return enriched

    def _derive_missing_financial_values(self, reports: list[dict]) -> list[dict]:
        """Derive common absolute values from enriched ratios when possible."""

        derived: list[dict] = []
        for row in reports:
            item = dict(row)
            revenue = item.get("revenue")
            net_margin = item.get("net_margin")
            if item.get("net_income") is None and revenue is not None and net_margin is not None:
                item["net_income"] = float(revenue) * float(net_margin)
            net_income = item.get("net_income")
            total_equity = item.get("total_equity")
            total_assets = item.get("total_assets")
            if item.get("roe") is None and net_income is not None and total_equity:
                item["roe"] = float(net_income) / float(total_equity)
            if item.get("roa") is None and net_income is not None and total_assets:
                item["roa"] = float(net_income) / float(total_assets)
            derived.append(item)
        return derived

    def _build_latest_metrics(self, reports: list[dict]) -> tuple[dict, dict]:
        """Build latest available metric values and record each metric's source fiscal year."""

        fields = (
            "revenue",
            "gross_margin",
            "net_margin",
            "roe",
            "roic",
            "net_income",
            "operating_cash_flow",
            "free_cash_flow",
            "shares_outstanding",
        )
        latest_metrics: dict[str, object] = {}
        latest_metric_years: dict[str, int | None] = {}
        for field in fields:
            value = None
            year = None
            for row in reversed(reports):
                if row.get(field) is not None:
                    value = row.get(field)
                    year = row.get("fiscal_year")
                    break
            latest_metrics[field] = value
            latest_metric_years[field] = year
        return latest_metrics, latest_metric_years
