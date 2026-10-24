"""ContextLoader node — lightweight preflight for the research workflow."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.repositories.company_repository import get_company_by_ticker, get_latest_company_profile, get_or_create_company, get_peer_mappings
from app.db.repositories.filing_repository import get_filings, is_effective_filing
from app.graph.state import ResearchState
from app.providers.company_info_provider import get_canonical_company_name
from app.services.peer_intelligence_service import PeerIntelligenceService
from app.services.section_cache_service import SectionCacheService
from app.utils.metadata_utils import normalize_ticker

logger = get_logger(__name__)


class ContextLoaderNode:
    """Load cached data, check filing readiness, and populate baseline context.

    This runs before any analysis nodes so downstream can skip redundant work.
    """

    def __init__(
        self,
        session: Session,
        cache_service: SectionCacheService | None = None,
        peer_intelligence: PeerIntelligenceService | None = None,
    ) -> None:
        self.session = session
        self.cache_service = cache_service or SectionCacheService()
        self.peer_intelligence = peer_intelligence or PeerIntelligenceService()

    def __call__(self, state: ResearchState) -> dict:
        ticker = normalize_ticker(state["ticker"])
        company_name = get_canonical_company_name(ticker, state["company_name"])
        company = get_or_create_company(self.session, ticker, company_name)

        filings = get_filings(self.session, company.id)
        effective = [f for f in filings if is_effective_filing(f)]
        has_filings = len(effective) > 0

        cached = self.cache_service.load_cached_state(self.session, company)

        stored_profile = get_latest_company_profile(self.session, company.id)
        stored_peers = get_peer_mappings(self.session, company.id)

        context: dict = {
            "ticker": ticker,
            "company_name": company_name,
            "warnings": [],
        }

        cached_profile = cached.get("company_profile", {})
        top_peers = self.peer_intelligence.discover_top_peers_fast(
            ticker=ticker,
            company_name=company_name,
            company_profile=cached_profile or None,
            limit=3,
        )

        context["_context"] = {
            "company_id": company.id,
            "has_effective_filings": has_filings,
            "effective_filing_count": len(effective),
            "total_filing_count": len(filings),
            "has_stored_profile": stored_profile is not None,
            "has_stored_peers": len(stored_peers) > 0,
            "cached_sections": list(cached.keys()),
            "top_peers": top_peers,
        }

        if not has_filings:
            context["warnings"].append(
                "No indexed filings found. Run /research/prewarm to ingest filings in the background."
            )

        for key, value in cached.items():
            if key not in state or not state.get(key):
                context[key] = value

        if top_peers:
            logger.info(
                "early_peers_discovered",
                ticker=ticker,
                peers=[p.get("ticker") for p in top_peers],
            )

        logger.info(
            "context_loaded",
            ticker=ticker,
            filings=len(effective),
            cached_sections=len(cached),
            has_profile=stored_profile is not None,
            top_peers=len(top_peers),
        )
        return context
