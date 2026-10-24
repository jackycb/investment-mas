"""PeerFilingIngester node — download and index annual reports for top peer companies."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.repositories.company_repository import get_or_create_company
from app.db.repositories.filing_repository import get_filings, is_effective_filing
from app.graph.state import ResearchState
from app.providers.company_info_provider import get_canonical_company_name
from app.services.filing_service import FilingService
from app.utils.metadata_utils import infer_market_from_ticker, normalize_ticker

logger = get_logger(__name__)


class PeerFilingIngesterNode:
    """Ingest the latest annual report for each top peer identified by ContextLoader.

    Runs as a parallel branch alongside BusinessAnalyst, NewsCollector, and
    FinancialAnalyst.  By the time PeerAnalyzer runs, peer filing chunks are
    available for financial metric extraction via RAG.
    """

    def __init__(self, session: Session, filing_service: FilingService | None = None) -> None:
        self.session = session
        self.filing_service = filing_service or FilingService()

    def __call__(self, state: ResearchState) -> dict:
        ctx = state.get("_context", {})
        top_peers = ctx.get("top_peers", [])

        if not top_peers:
            return {"warnings": ["No top peers identified for filing ingestion."]}

        ingested_peers: list[str] = []
        warnings: list[str] = []

        for peer in top_peers:
            peer_ticker = normalize_ticker(str(peer.get("ticker", "")))
            peer_name = str(peer.get("company_name", "") or peer_ticker.split(".")[-1])
            if not peer_ticker:
                continue

            try:
                canonical_name = get_canonical_company_name(peer_ticker, peer_name)
                peer_company = get_or_create_company(self.session, peer_ticker, canonical_name)

                existing = get_filings(self.session, peer_company.id)
                effective = [f for f in existing if is_effective_filing(f)]
                if effective:
                    logger.info("peer_filings_exist", ticker=peer_ticker, count=len(effective))
                    ingested_peers.append(peer_ticker)
                    continue

                market = infer_market_from_ticker(peer_ticker)
                filings, ing_warnings = self.filing_service.ingest_company_filings(
                    session=self.session,
                    company=peer_company,
                    market=market,
                    years=1,
                    filing_types={"annual_report"},
                )
                warnings.extend(ing_warnings)

                effective_new = [f for f in filings if is_effective_filing(f)]
                if effective_new:
                    ingested_peers.append(peer_ticker)
                    logger.info("peer_filing_ingested", ticker=peer_ticker, filings=len(effective_new))
                else:
                    warnings.append(f"No annual report found for peer {peer_ticker}.")
            except Exception as exc:
                logger.warning("peer_filing_ingestion_failed", ticker=peer_ticker, error=str(exc))
                warnings.append(f"Failed to ingest filings for peer {peer_ticker}: {exc}")

        logger.info("peer_filing_ingestion_complete", ingested=ingested_peers, count=len(ingested_peers))

        return {
            "warnings": warnings,
        }
