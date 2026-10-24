"""PeerAnalyst node — V3: plan-driven peer analysis with optional skip.

Peer market metrics now come from ``market_data_skill``, which shares one cached
Yahoo Finance fetch with ``FinancialService.compare_peers``. Previously this
node and FinancialAnalyst each paid a 5–11 s network fetch for overlapping
ticker sets; the snapshot shape produced here is unchanged.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.state import ResearchState
from app.services.peer_analysis_service import PeerAnalysisService
from app.services.peer_intelligence_service import PeerIntelligenceService
from app.skills.market_data import dedupe_tickers, fetch_peer_market_data

#: Exactly the fields this node exposed before the market-data skill existed.
PEER_SNAPSHOT_FIELDS = (
    "company_name",
    "currency",
    "revenue",
    "revenue_growth",
    "gross_margin",
    "net_margin",
    "roe",
    "roic",
    "free_cash_flow",
)


class PeerAnalystNode:
    """Classify and explain peer relationships based on multi-source evidence.

    V3 additions:
    - Reads skip/focus_areas from the research plan.
    - When skip is set, returns empty peer_analysis cleanly.
    - focus_areas may narrow which peer dimensions to analyze.
    """

    def __init__(
        self,
        session: Session | None = None,
        service: PeerAnalysisService | None = None,
        intelligence_service: PeerIntelligenceService | None = None,
        model: str | None = None,
    ) -> None:
        self.session = session
        self.service = service or PeerAnalysisService()
        self.intelligence_service = intelligence_service or PeerIntelligenceService()
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        plan = state.get("research_plan", {})
        peer_cfg = plan.get("sections", {}).get("peer_analysis", {})

        if peer_cfg.get("skip"):
            return {"peer_analysis": {}}

        try:
            profile = state.get("company_profile", {})

            # V3: Use plan's peer_candidates instead of discovering peers dynamically
            plan_peers = plan.get("peer_candidates", [])
            if plan_peers:
                merged_peers = plan_peers
            else:
                # Fallback: use existing peer_list from state
                merged_peers = state.get("peer_list", [])

            target_upper = state["ticker"].upper()
            peer_tickers = [
                str(p.get("ticker", ""))
                for p in merged_peers
                if p.get("ticker") and str(p["ticker"]).upper() != target_upper
            ]

            all_tickers = self._dedupe_tickers([state["ticker"]] + peer_tickers)
            market = fetch_peer_market_data(
                all_tickers,
                force_refresh=bool(state.get("force_refresh", False)),
                snapshot_fields=PEER_SNAPSHOT_FIELDS,
            )
            peer_metrics: dict[str, dict] = dict(market.snapshots)

            # Fallback to local data for peers the market source could not resolve
            unfetched = [t for t in peer_tickers if t not in peer_metrics]
            if unfetched:
                local_metrics = self.intelligence_service.load_peer_metrics(unfetched)
                for ticker, metrics in local_metrics.items():
                    peer_metrics.setdefault(ticker, metrics)

            result = self.service.analyze(
                ticker=state["ticker"],
                company_name=state["company_name"],
                company_profile=profile,
                peer_list=merged_peers,
                business_analysis=state.get("business_analysis", {}),
                model=self.model,
            )

            if peer_metrics:
                result["peer_metric_snapshots"] = peer_metrics

            warnings: list[str] = list(market.warnings)
        except Exception as exc:
            return {
                "peer_analysis": {},
                "errors": [f"PeerAnalyst failed: {exc}"],
            }
        return {
            "peer_analysis": result,
            "warnings": warnings,
        }

    @staticmethod
    def _dedupe_tickers(tickers: list[str]) -> list[str]:
        """Remove duplicate tickers that resolve to the same Yahoo symbol."""

        return dedupe_tickers(tickers)

    def _merge_with_existing(
        self,
        existing: list[dict],
        enriched: list[dict],
    ) -> list[dict]:
        """Merge enriched candidates with the original peer_list, deduplicating by ticker."""

        seen: dict[str, dict] = {}
        for p in existing:
            t = str(p.get("ticker", "")).upper()
            if t:
                seen[t] = p
        for p in enriched:
            t = str(p.get("ticker", "")).upper()
            if t and t not in seen:
                seen[t] = p
            elif t and t in seen:
                old = seen[t]
                old["similarity_score"] = max(
                    float(old.get("similarity_score", 0)),
                    float(p.get("similarity_score", 0)),
                )
                if not old.get("rationale") and p.get("rationale"):
                    old["rationale"] = p["rationale"]
                if p.get("source"):
                    sources = old.get("sources", [])
                    sources.append(p["source"])
                    old["sources"] = sources
        return list(seen.values())[:12]

# Backward-compat alias
PeerAnalyzerNode = PeerAnalystNode
