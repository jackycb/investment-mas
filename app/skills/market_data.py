"""market_data_skill — one cached source of peer market metrics.

Optimization task §4.1 / §6.1 / §8. Measured baseline: a single research run
called ``fetch_yahoo_peer_financials`` twice for nearly the same ticker set —
once from ``FinancialService.compare_peers`` (FinancialAnalyst) and once from
``PeerAnalystNode`` — costing 5.6–11.2 s each with no reuse.

The caching itself lives in :mod:`app.providers.financial_data_provider` so that
**every** existing call site benefits without being rewritten. This module is
the explicit, documented entry point for new code and adds:

  * deterministic ticker normalization and de-duplication before fetching,
  * a ``peer_metric_snapshots`` shape matching what ``PeerAnalystNode`` builds,
  * a local-data fallback for tickers the market source cannot resolve,
  * telemetry around the network portion.

No numeric value is computed here — this is pure retrieval plus shaping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.providers.financial_data_provider import _to_yahoo_symbol, fetch_yahoo_peer_financials
from app.skills.base import skill_span

logger = get_logger(__name__)

#: Fields exposed in the peer snapshot, matching PeerAnalystNode's existing shape.
SNAPSHOT_FIELDS = (
    "company_name",
    "currency",
    "revenue",
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "roe",
    "roic",
    "free_cash_flow",
)

#: Defaults preserved from PeerAnalystNode's original inline snapshot building,
#: so the payload stays byte-identical for existing report rendering.
SNAPSHOT_DEFAULTS = {"company_name": None, "currency": "USD"}


@dataclass
class MarketDataResult:
    """Peer market metrics plus retrieval provenance."""

    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, list[dict]] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.snapshots)


def dedupe_tickers(tickers: list[str]) -> list[str]:
    """Drop tickers that map to the same market symbol (existing rule)."""

    seen: set[str] = set()
    result: list[str] = []
    for ticker in tickers:
        if not ticker:
            continue
        symbol = (_to_yahoo_symbol(ticker) or ticker).upper()
        if symbol not in seen:
            seen.add(symbol)
            result.append(ticker)
    return result


def fetch_peer_market_data(
    tickers: list[str],
    *,
    force_refresh: bool = False,
    snapshot_fields: tuple[str, ...] = SNAPSHOT_FIELDS,
) -> MarketDataResult:
    """Return cached peer market metrics for *tickers*.

    Repeated calls within the cache TTL cost ~0.4 ms instead of 5–11 s
    (measured), which is what removes the duplicate fetch from a research run.
    """

    unique = dedupe_tickers(list(tickers or []))
    result = MarketDataResult(requested=list(unique))
    if not unique:
        return result

    with skill_span("market_data", span_kind="io") as record:
        raw = fetch_yahoo_peer_financials(unique, force_refresh=force_refresh)
        record.detail = f"requested={len(unique)} resolved={sum(1 for v in raw.values() if v)}"

    result.raw = raw
    for ticker in unique:
        records = raw.get(ticker) or []
        if not records:
            result.unresolved.append(ticker)
            continue
        row = records[0]
        snapshot: dict[str, Any] = {}
        for name in snapshot_fields:
            value = row.get(name)
            if value is None:
                # Preserve the exact defaults PeerAnalystNode used inline before
                # this skill existed: company_name fell back to the ticker and
                # currency to USD.
                if name == "company_name":
                    value = ticker
                elif name == "currency":
                    value = SNAPSHOT_DEFAULTS["currency"]
            snapshot[name] = value
        snapshot["data_source"] = "yahoo_finance_ttm"
        result.snapshots[ticker] = snapshot
        result.resolved.append(ticker)

    if result.unresolved:
        result.warnings.append(f"market data unavailable for: {', '.join(result.unresolved)}")
    logger.info(
        "market_data_skill_done",
        requested=len(unique),
        resolved=len(result.resolved),
        unresolved=len(result.unresolved),
    )
    return result
