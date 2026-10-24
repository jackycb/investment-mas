"""DataFetcher node — on-demand data ingestion driven by the research plan.

Delegates filing work to ``financial_report_ingestion_skill``, which wraps the
existing ``FilingService`` and adds cross-task deduplication plus separate
telemetry for discovery/download/parse. Peer filings still skip the vector
store (``NullVectorStoreProvider``) so structured financial analysis is never
blocked by vector indexing.

The node's return contract is unchanged: ``fetch_results`` keeps the
``target_filings_fetched`` / ``peer_filings_fetched`` / ``news_fetched`` /
``fetch_errors`` keys existing callers and the frontend already read.
"""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.graph.state import ResearchState
from app.providers.vector_store_provider import NullVectorStoreProvider
from app.services.filing_service import FilingService
from app.services.news_service import NewsService
from app.skills.financial_report_ingestion import FinancialReportIngestionSkill

logger = get_logger(__name__)


class DataFetcherNode:
    """Fetch missing data based on the data_availability check.

    Only fetches data that is:
    1. Required by the research plan
    2. Not already available

    Peer filings use a ``NullVectorStoreProvider`` to skip the vector upsert —
    chunks in PostgreSQL are sufficient for PeerAnalyst's metric extraction, and
    structured financial analysis must never be blocked by vector indexing.
    """

    def __init__(self, session: Session, filing_service: FilingService | None = None) -> None:
        self.session = session
        self.filing_service = filing_service or FilingService()
        # Must stay NullVectorStoreProvider: passing None would make FilingService
        # lazily build the real (Chroma) store and re-introduce the slow upsert.
        self._peer_filing_service = FilingService(vector_store=NullVectorStoreProvider())
        self.skill = FinancialReportIngestionSkill(
            filing_service=self.filing_service,
            no_index_filing_service=self._peer_filing_service,
        )

    def __call__(self, state: ResearchState) -> dict:
        availability = state.get("data_availability", {})
        fetch_needed = availability.get("fetch_needed", {})
        force_refresh = bool(state.get("force_refresh", False))

        fetched = {
            "target_filings_fetched": False,
            "peer_filings_fetched": [],
            "news_fetched": False,
            "fetch_errors": [],
            "ingestion": {},
        }

        # --- Target company annual reports (full pipeline with vector store) ---
        if fetch_needed.get("target_filings"):
            try:
                result = self.skill.ingest(
                    self.session,
                    state["ticker"],
                    state["company_name"],
                    years=1,
                    filing_types={"annual_report"},
                    index_vectors=True,
                    force_refresh=force_refresh,
                )
                fetched["target_filings_fetched"] = True
                fetched["fetch_errors"].extend(result.warnings)
                fetched["ingestion"][state["ticker"]] = result.to_dict()
                logger.info(
                    "data_fetcher_target_filings_ok",
                    ticker=state["ticker"],
                    counts=result.counts(),
                )
            except Exception as exc:
                fetched["fetch_errors"].append(f"Target filings fetch failed for {state['ticker']}: {exc}")
                logger.warning("data_fetcher_target_filings_failed", ticker=state["ticker"], error=str(exc))

        # --- Peer annual reports (lightweight: no vector index) ---
        peer_filings_needed = fetch_needed.get("peer_filings", [])
        if peer_filings_needed:
            logger.info(
                "data_fetcher_peer_filings_start",
                count=len(peer_filings_needed),
                tickers=peer_filings_needed,
            )
            peer_results = self.skill.ingest_many(
                self.session,
                peer_filings_needed,
                years=1,
                filing_types={"annual_report"},
                index_vectors=False,
            )
            for peer_ticker, result in peer_results.items():
                fetched["ingestion"][peer_ticker] = result.to_dict()
                fetched["fetch_errors"].extend(result.warnings)
                if result.status == "failed":
                    fetched["fetch_errors"].append(f"Peer filings fetch failed for {peer_ticker}")
                    logger.warning("data_fetcher_peer_filings_failed", ticker=peer_ticker)
                else:
                    fetched["peer_filings_fetched"].append(peer_ticker)
                    logger.info("data_fetcher_peer_filings_ok", ticker=peer_ticker, counts=result.counts())

        # --- News ---
        if fetch_needed.get("news"):
            try:
                news_service = NewsService()
                news_service.collect_news(self.session, state["ticker"], state["company_name"], [])
                fetched["news_fetched"] = True
                logger.info("data_fetcher_news_ok", ticker=state["ticker"])
            except Exception as exc:
                fetched["fetch_errors"].append(f"News fetch failed for {state['ticker']}: {exc}")
                logger.warning("data_fetcher_news_failed", ticker=state["ticker"], error=str(exc))

        return {
            "fetch_results": fetched,
            # Restored contract: downstream nodes (SynthesisReviewer, coverage
            # checkers) judge filing/news coverage from ``data_availability``.
            # Returning the refreshed view stops them from scoring the run
            # against a pre-fetch snapshot.
            "data_availability": self._refresh_availability(availability, fetched),
            "warnings": fetched["fetch_errors"],
        }

    @staticmethod
    def _refresh_availability(availability: dict, fetched: dict) -> dict:
        """Copy *availability* and mark what this node actually fetched.

        The incoming dict is deep-copied: state snapshots are shared with
        parallel branches, so a node must never mutate one in place.
        """

        updated = deepcopy(dict(availability or {}))
        fetch_needed = updated.setdefault("fetch_needed", {})

        if fetched.get("target_filings_fetched"):
            updated.setdefault("target", {}).setdefault("filings", {})["status"] = "available"
            fetch_needed["target_filings"] = False

        peers_fetched = list(fetched.get("peer_filings_fetched") or [])
        if peers_fetched:
            peers = updated.setdefault("peers", {})
            for ticker in peers_fetched:
                peers.setdefault(ticker, {}).setdefault("filings", {})["status"] = "available"
            pending = fetch_needed.get("peer_filings")
            if isinstance(pending, list):
                fetch_needed["peer_filings"] = [t for t in pending if t not in set(peers_fetched)]

        if fetched.get("news_fetched"):
            updated.setdefault("news", {})["status"] = "fresh"
            fetch_needed["news"] = False

        return updated