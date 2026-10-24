"""DataAvailabilityChecker node — deterministic pre-flight data inventory."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.repositories.company_repository import get_or_create_company, get_latest_company_profile
from app.db.repositories.filing_repository import get_effective_filings, get_filings
from app.db.repositories.news_repository import get_recent_articles
from app.graph.state import ResearchState
from app.services.section_cache_service import SectionCacheService

logger = get_logger(__name__)

MAX_NEWS_AGE_HOURS = 24


class DataAvailabilityCheckerNode:
    """Check what data is already available and compute what needs fetching.

    No LLM. Purely deterministic DB/filesystem queries.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def __call__(self, state: ResearchState) -> dict:
        plan = state.get("research_plan", {})
        ticker = state["ticker"]
        company_name = state["company_name"] or ticker

        company = get_or_create_company(self.session, ticker, company_name)

        target = self._check_target(company)
        peers = self._check_peers(plan.get("peer_candidates", []))
        news = self._check_news(company)
        section_cache = self._check_section_cache(company)

        fetch_needed = self._compute_fetch_needed(plan, target, peers, news)

        availability = {
            "company_id": company.id,
            "target": target,
            "peers": peers,
            "news": news,
            "section_cache": section_cache,
            "fetch_needed": fetch_needed,
        }

        filing_coverage = self._build_filing_coverage(target)

        top_peers = plan.get("peer_candidates", [])

        context = {
            "company_id": company.id,
            "has_effective_filings": target["filings"]["status"] == "available",
            "effective_filing_count": target["filings"].get("effective_count", 0),
            "total_filing_count": target["filings"].get("total_count", 0),
            "has_stored_profile": target["company_profile"]["status"] == "available",
            "cached_sections": list(section_cache.keys()),
            "top_peers": top_peers,
        }

        result = {
            "data_availability": availability,
            "filing_coverage": filing_coverage,
            "_context": context,
        }

        # Restore cached section data into state so downstream nodes can skip redundant work
        # (same behavior as old ContextLoaderNode)
        for key, value in section_cache.items():
            if value:  # only restore non-empty cached sections
                result[key] = value

        return result

    def _check_target(self, company) -> dict:
        """Check all data categories for the target company."""
        effective = get_effective_filings(self.session, company.id)
        all_filings = get_filings(self.session, company.id)
        profile = get_latest_company_profile(self.session, company.id)

        return {
            "company_id": company.id,
            "ticker": company.ticker,
            "filings": {
                "status": "available" if effective else "missing",
                "effective_count": len(effective),
                "total_count": len(all_filings),
            },
            "company_profile": {
                "status": "available" if profile else "missing",
            },
            "basic_info": {
                "status": "available",
            },
        }

    def _check_peers(self, peer_candidates: list[dict]) -> dict[str, dict]:
        """Check filing availability for each peer candidate."""
        result: dict[str, dict] = {}
        for peer in peer_candidates:
            peer_ticker = peer.get("ticker", "")
            if not peer_ticker:
                continue
            peer_name = peer.get("company_name", peer_ticker)
            peer_company = get_or_create_company(self.session, peer_ticker, peer_name)
            effective = get_effective_filings(self.session, peer_company.id)
            result[peer_ticker] = {
                "company_id": peer_company.id,
                "filings": {
                    "status": "available" if effective else "missing",
                    "effective_count": len(effective),
                },
            }
        return result

    def _check_news(self, company) -> dict:
        """Check if recent cached news is fresh enough to skip re-fetch."""
        articles = get_recent_articles(self.session, company.id, limit=5)
        has_recent = len(articles) > 0
        return {
            "status": "fresh" if has_recent else "stale",
            "article_count": len(articles),
        }

    def _check_section_cache(self, company) -> dict:
        """Check which sections have valid cached data."""
        cache_service = SectionCacheService()
        cached = cache_service.load_cached_state(self.session, company)
        return cached

    def _compute_fetch_needed(self, plan: dict, target: dict, peers: dict[str, dict], news: dict) -> dict:
        """Compare plan requirements against availability to determine what to fetch."""
        data_req = plan.get("data_requirements", {})

        fetch_needed = {
            "target_filings": False,
            "peer_filings": [],
            "news": False,
        }

        # Target filings: fetch if required and missing
        if data_req.get("target_filing") and target["filings"]["status"] == "missing":
            fetch_needed["target_filings"] = True

        # Peer filings: fetch for each required peer that's missing
        required_peer_filings = data_req.get("peer_filings", [])
        for peer_ticker in required_peer_filings:
            peer_data = peers.get(peer_ticker, {})
            if peer_data.get("filings", {}).get("status") != "available":
                fetch_needed["peer_filings"].append(peer_ticker)

        # News: fetch if required and stale
        if data_req.get("recent_news") and news.get("status") == "stale":
            fetch_needed["news"] = True

        return fetch_needed

    def _build_filing_coverage(self, target: dict) -> dict:
        """Build filing_coverage for backward compatibility with existing nodes."""
        filings_data = target.get("filings", {})
        overall = "sufficient" if filings_data.get("effective_count", 0) > 0 else "insufficient"
        return {
            "overall_coverage": overall,
            "covered": {},
            "missing": ["annual_report"] if filings_data.get("effective_count", 0) == 0 else [],
        }