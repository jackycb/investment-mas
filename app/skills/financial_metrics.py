"""financial_metrics_skill — deterministic financial indicator computation.

Optimization task §4.2. This skill is a **thin cache + shaping layer over the
existing :class:`FinancialService`**; no formula that already exists there is
re-implemented here.

Provenance of every exposed metric is declared explicitly:

  * ``reused``      — computed by existing code (``FinancialService`` /
                      ``financial_mapping.finalize_financial_row``).
  * ``derived``     — a deterministic ratio computed here from fields the
                      existing pipeline already normalizes. No new data source.
  * ``unavailable`` — the required raw field does not exist in the current data
                      model. Per §4.2 rule 7 the skill reports that status
                      instead of filling in a guess.

Units / caliber: monetary values keep the reporting currency of the source
annual report (see ``currency``); ratios are decimals (0.42 == 42%), not
percentages; every metric carries the ``fiscal_year`` it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.providers.financial_data_provider import build_financial_provider
from app.services.compute_cache_service import CACHE_METRICS, cache_key, cached_compute
from app.services.financial_service import FinancialService
from app.skills.base import PermanentSkillError, skill_span

logger = get_logger(__name__)

#: Metric catalog with declared provenance so callers and tests can assert that
#: nothing is silently fabricated.
METRIC_CATALOG: dict[str, dict[str, str]] = {
    "revenue_growth": {"provenance": "derived", "unit": "ratio", "source_field": "revenue"},
    "revenue_cagr": {"provenance": "reused", "unit": "ratio", "source_field": "revenue"},
    "gross_margin": {"provenance": "reused", "unit": "ratio", "source_field": "gross_margin"},
    "operating_margin": {"provenance": "derived", "unit": "ratio", "source_field": "operating_profit/revenue"},
    "net_margin": {"provenance": "reused", "unit": "ratio", "source_field": "net_margin"},
    "eps": {"provenance": "derived", "unit": "currency_per_share", "source_field": "net_income/shares_outstanding"},
    "roe": {"provenance": "reused", "unit": "ratio", "source_field": "roe"},
    "roic": {"provenance": "reused", "unit": "ratio", "source_field": "roic"},
    "operating_cash_flow": {"provenance": "reused", "unit": "currency", "source_field": "operating_cash_flow"},
    "free_cash_flow": {"provenance": "reused", "unit": "currency", "source_field": "free_cash_flow"},
    "cash_conversion": {"provenance": "reused", "unit": "ratio", "source_field": "ocf_vs_net_income.ratio"},
    "total_debt": {"provenance": "reused", "unit": "currency", "source_field": "total_liabilities"},
    "debt_ratio": {"provenance": "reused", "unit": "ratio", "source_field": "debt_ratio"},
    "net_debt": {"provenance": "unavailable", "unit": "currency", "source_field": "requires cash_and_equivalents"},
    "capital_intensity": {"provenance": "unavailable", "unit": "ratio", "source_field": "requires capital_expenditure"},
    "share_dilution": {"provenance": "derived", "unit": "ratio", "source_field": "shares_outstanding"},
    "anomaly_signals": {"provenance": "reused", "unit": "list", "source_field": "identify_red_flags"},
}

DEFAULT_YEARS = 5

TABLE_METRICS = (
    "revenue",
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "eps",
    "roe",
    "roic",
    "operating_cash_flow",
    "free_cash_flow",
    "cash_conversion",
    "total_debt",
    "debt_ratio",
    "share_dilution",
    "net_debt",
    "capital_intensity",
)


def _safe_div(numerator: Any, denominator: Any) -> float | None:
    """Divide only when both operands are usable and the denominator is non-zero."""

    try:
        if numerator is None or denominator is None:
            return None
        num = float(numerator)
        den = float(denominator)
    except (TypeError, ValueError):
        return None
    if den == 0:
        return None
    return num / den


def _series(rows: list[dict], field_name: str) -> list[tuple[int, float | None]]:
    """Return an ordered ``(fiscal_year, value)`` series, oldest first."""

    out: list[tuple[int, float | None]] = []
    for row in rows:
        year = row.get("fiscal_year")
        if year is None:
            continue
        value = row.get(field_name)
        try:
            out.append((int(year), None if value is None else float(value)))
        except (TypeError, ValueError):
            out.append((int(year), None))
    return out


def _yoy(series: list[tuple[int, float | None]]) -> dict[int, float | None]:
    """Year-over-year change keyed by fiscal year; ``None`` when unusable."""

    changes: dict[int, float | None] = {}
    for index in range(1, len(series)):
        year, value = series[index]
        _prev_year, prev_value = series[index - 1]
        ratio = None
        if value is not None and prev_value not in (None, 0):
            ratio = (value - prev_value) / abs(prev_value)
        changes[year] = ratio
    return changes


def _cagr_by_year(series: list[tuple[int, float | None]]) -> dict[int, float | None]:
    """CAGR from the first usable year to each later year (existing caliber)."""

    base = next(((y, v) for y, v in series if v and v > 0), None)
    if base is None:
        return {}
    base_year, base_value = base
    out: dict[int, float | None] = {}
    for year, value in series:
        periods = year - base_year
        out[year] = ((value / base_value) ** (1 / periods) - 1) if value and value > 0 and periods > 0 else None
    return out


@dataclass
class FinancialMetricsResult:
    """Structured, cache-aware output consumed by FinancialAnalyst/ValuationAgent."""

    ticker: str
    company_name: str
    status: str = "succeeded"  # succeeded | no_data | failed
    years: list[int] = field(default_factory=list)
    currency: str | None = None
    unit: str = "reporting_currency_absolute"
    period_type: str = "fiscal_year"
    data_source: str = "unavailable"
    #: Raw normalized annual rows (oldest → newest), unchanged from the provider.
    reports: list[dict] = field(default_factory=list)
    #: Existing FinancialService summary — reused verbatim, never recomputed.
    financial_summary: dict = field(default_factory=dict)
    #: Peer comparison produced by FinancialService.compare_peers.
    peer_comparison: dict = field(default_factory=dict)
    #: Red flags produced by FinancialService.identify_red_flags.
    red_flags: list[str] = field(default_factory=list)
    #: Deterministic per-year metric table built by this skill.
    metrics_by_year: list[dict[str, Any]] = field(default_factory=list)
    #: Latest available value per metric plus the fiscal year it came from.
    latest: dict[str, Any] = field(default_factory=dict)
    #: metric -> fiscal years where the value is missing. Never filled in.
    missing: dict[str, list[int]] = field(default_factory=dict)
    #: metric -> provenance string from METRIC_CATALOG.
    provenance: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cache_hit: bool = False
    cache_key: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    def as_state_patch(self) -> dict[str, Any]:
        """Shape the payload the existing workflow nodes already expect."""

        patch: dict[str, Any] = {
            "financial_summary": self.financial_summary,
            "peer_comparison": self.peer_comparison,
            "financial_red_flags": self.red_flags,
        }
        if self.warnings:
            patch["warnings"] = list(self.warnings)
        return patch

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "status": self.status,
            "years": self.years,
            "currency": self.currency,
            "unit": self.unit,
            "period_type": self.period_type,
            "data_source": self.data_source,
            "metrics_by_year": self.metrics_by_year,
            "latest": self.latest,
            "missing": self.missing,
            "provenance": self.provenance,
            "warnings": self.warnings,
            "errors": self.errors,
            "cache_hit": self.cache_hit,
        }



class FinancialMetricsSkill:
    """Reusable, cache-aware financial metric computation.

    Wraps :class:`FinancialService` — the existing owner of every formula — and
    adds (a) result caching keyed on the underlying data version, (b) a uniform
    per-year metric table, and (c) explicit missing-value reporting.
    """

    def __init__(self, session: Session | None = None, service: FinancialService | None = None) -> None:
        self.session = session
        self.service = service or (
            FinancialService(provider=build_financial_provider(session)) if session is not None else None
        )

    def compute(
        self,
        ticker: str,
        company_name: str,
        peers: list[dict] | None = None,
        *,
        force_refresh: bool = False,
        with_peer_comparison: bool = True,
    ) -> FinancialMetricsResult:
        """Compute (or reuse) the full metric set for *ticker*.

        ``with_peer_comparison=False`` skips the peer network fetch entirely, so
        structured analysis of the target is never blocked by peer I/O (§6.3).
        """

        if not ticker:
            raise PermanentSkillError("financial_metrics_skill requires a non-empty ticker")
        service = self.service or FinancialService(provider=build_financial_provider(self.session))
        peer_key = tuple(sorted(str(p.get("ticker", "")) for p in (peers or []) if p.get("ticker")))
        fingerprint = self._data_fingerprint(ticker)
        key = cache_key("financial_metrics", ticker, DEFAULT_YEARS, peer_key, with_peer_comparison, fingerprint)

        with skill_span("financial_metrics") as record:
            payload, hit = cached_compute(
                CACHE_METRICS,
                key,
                lambda: self._compute_uncached(
                    service, ticker, company_name, peers or [], with_peer_comparison=with_peer_comparison
                ),
                force_refresh=force_refresh,
                # Never cache a hard failure: a later run must retry it.
                cache_ok=lambda value: value.get("status") != "failed",
            )
            record.cache_hit = hit

        allowed = set(FinancialMetricsResult.__dataclass_fields__) - {"ticker", "company_name", "cache_hit", "cache_key"}
        result = FinancialMetricsResult(
            ticker=ticker,
            company_name=company_name,
            cache_hit=hit,
            cache_key=key,
            **{k: v for k, v in payload.items() if k in allowed},
        )
        logger.info(
            "financial_metrics_skill_done",
            ticker=ticker,
            status=result.status,
            years=result.years,
            cache_hit=hit,
            metric_years=len(result.metrics_by_year),
        )
        return result


    def _compute_uncached(
        self,
        service: FinancialService,
        ticker: str,
        company_name: str,
        peers: list[dict],
        *,
        with_peer_comparison: bool,
    ) -> dict[str, Any]:
        try:
            financial_summary, reports, warnings = service.summarize_company(ticker, company_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("financial_metrics_skill_failed", ticker=ticker, error=str(exc))
            return {
                "status": "failed",
                "financial_summary": {},
                "peer_comparison": {},
                "red_flags": [],
                "reports": [],
                "years": [],
                "metrics_by_year": [],
                "latest": {},
                "missing": {},
                "provenance": {k: v["provenance"] for k, v in METRIC_CATALOG.items()},
                "warnings": [],
                "errors": [f"financial_metrics_skill failed: {exc}"],
                "data_source": "unavailable",
            }

        peer_comparison: dict[str, Any] = {}
        red_flags: list[str] = []
        try:
            if with_peer_comparison:
                peer_comparison = service.compare_peers(ticker, company_name, reports, peers)
            red_flags = service.identify_red_flags(financial_summary, peer_comparison)
        except Exception as exc:  # noqa: BLE001
            warnings = [*warnings, f"peer comparison unavailable: {exc}"]

        metrics_by_year, latest, missing = self._build_metric_table(reports, financial_summary)
        return {
            "status": "succeeded" if reports else "no_data",
            "financial_summary": financial_summary,
            "peer_comparison": peer_comparison,
            "red_flags": red_flags,
            "reports": reports,
            "years": [int(row["fiscal_year"]) for row in reports if row.get("fiscal_year") is not None],
            "currency": financial_summary.get("currency") or financial_summary.get("quote_currency"),
            "data_source": financial_summary.get("data_source", "unavailable"),
            "metrics_by_year": metrics_by_year,
            "latest": latest,
            "missing": missing,
            "provenance": {k: v["provenance"] for k, v in METRIC_CATALOG.items()},
            "warnings": list(warnings or []),
            "errors": [],
        }

    def _build_metric_table(
        self, reports: list[dict], financial_summary: dict
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, list[int]]]:
        """Build the per-year table plus latest/missing views from existing rows."""

        revenue_growth = _yoy(_series(reports, "revenue"))
        share_dilution = _yoy(_series(reports, "shares_outstanding"))
        cagr_by_year = _cagr_by_year(_series(reports, "revenue"))
        cash_conversion = {
            int(item["fiscal_year"]): item.get("ratio")
            for item in financial_summary.get("ocf_vs_net_income", [])
            if item.get("fiscal_year") is not None
        }

        metrics_by_year: list[dict[str, Any]] = []
        for row in reports:
            year = row.get("fiscal_year")
            if year is None:
                continue
            year = int(year)
            metrics_by_year.append(
                {
                    "fiscal_year": year,
                    "revenue": row.get("revenue"),
                    "revenue_growth": revenue_growth.get(year),
                    "revenue_cagr_since_first_year": cagr_by_year.get(year),
                    "gross_margin": row.get("gross_margin"),
                    "operating_margin": _safe_div(row.get("operating_profit"), row.get("revenue")),
                    "net_margin": row.get("net_margin"),
                    "eps": _safe_div(row.get("net_income"), row.get("shares_outstanding")),
                    "roe": row.get("roe"),
                    "roic": row.get("roic"),
                    "roa": row.get("roa"),
                    "operating_cash_flow": row.get("operating_cash_flow"),
                    "free_cash_flow": row.get("free_cash_flow"),
                    "cash_conversion": cash_conversion.get(year),
                    "total_debt": row.get("total_liabilities"),
                    "debt_ratio": row.get("debt_ratio"),
                    "share_dilution": share_dilution.get(year),
                    # Declared unavailable rather than approximated (§4.2 rule 7).
                    "net_debt": None,
                    "capital_intensity": None,
                }
            )

        latest, missing = self._latest_and_missing(metrics_by_year, financial_summary)
        return metrics_by_year, latest, missing


    @staticmethod
    def _latest_and_missing(
        metrics_by_year: list[dict[str, Any]], financial_summary: dict
    ) -> tuple[dict[str, Any], dict[str, list[int]]]:
        """Latest value per metric plus an explicit missing-year map (§4.2 rule 7)."""

        latest: dict[str, Any] = {}
        missing: dict[str, list[int]] = {}
        all_years = [int(m["fiscal_year"]) for m in metrics_by_year]
        for name in TABLE_METRICS:
            value = None
            value_year = None
            absent: list[int] = []
            for entry in reversed(metrics_by_year):
                if entry.get(name) is not None and value is None:
                    value = entry[name]
                    value_year = entry["fiscal_year"]
                if entry.get(name) is None:
                    absent.append(int(entry["fiscal_year"]))
            latest[name] = {
                "value": value,
                "fiscal_year": value_year,
                "status": "ok" if value is not None else "missing",
            }
            if value is None:
                missing[name] = list(all_years)
            elif absent:
                missing[name] = sorted(absent)

        cagr = financial_summary.get("revenue_cagr")
        latest["revenue_cagr"] = {
            "value": cagr,
            "fiscal_year": all_years[-1] if all_years else None,
            "status": "ok" if cagr is not None else "missing",
        }
        if cagr is None:
            missing["revenue_cagr"] = list(all_years)
        latest["anomaly_signals"] = {
            "value": list(financial_summary.get("warnings") or []),
            "status": "ok",
        }
        return latest, missing

    def _data_fingerprint(self, ticker: str) -> str:
        """Cheap data-version probe used in the cache key (§6.3).

        Uses indexed DB columns only. Without a session the fingerprint degrades
        to a constant and the TTL alone bounds staleness — documented rather
        than silently wrong.
        """

        if self.session is None:
            return "no-db"
        try:
            from sqlalchemy import func, select

            from app.db.models import FinancialReport
            from app.db.repositories.company_repository import get_company_by_ticker

            company = get_company_by_ticker(self.session, ticker)
            if company is None:
                return "no-company"
            row = self.session.execute(
                # FinancialReport has no ``updated_at``; ``created_at`` is the row's
                # only timestamp and reports are inserted (not mutated) per filing.
                select(func.count(FinancialReport.id), func.max(FinancialReport.created_at)).where(
                    FinancialReport.company_id == company.id
                )
            ).first()
            count, updated = (row or (0, None))
            return f"{count}:{updated.isoformat() if updated else '-'}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("financial_metrics_fingerprint_unavailable", ticker=ticker, error=str(exc)[:120])
            return "no-db"


_default_skill: FinancialMetricsSkill | None = None


def get_financial_metrics_skill(session: Session | None = None) -> FinancialMetricsSkill:
    """Return a skill bound to *session*, or a process-wide session-less default."""

    global _default_skill
    if session is not None:
        return FinancialMetricsSkill(session=session)
    if _default_skill is None:
        _default_skill = FinancialMetricsSkill()
    return _default_skill


def compute_financial_metrics(
    session: Session | None,
    ticker: str,
    company_name: str,
    peers: list[dict] | None = None,
    *,
    force_refresh: bool = False,
    with_peer_comparison: bool = True,
) -> FinancialMetricsResult:
    """Functional entry point for the financial metrics skill."""

    return get_financial_metrics_skill(session).compute(
        ticker,
        company_name,
        peers,
        force_refresh=force_refresh,
        with_peer_comparison=with_peer_comparison,
    )

