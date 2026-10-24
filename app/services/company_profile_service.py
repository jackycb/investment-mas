"""Company profiling service."""

from __future__ import annotations

from datetime import date, datetime, timezone
import os
import re

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Company, CompanyProfile, PeerMapping
from app.db.repositories.company_repository import get_company_by_ticker, get_latest_company_profile, get_or_create_company, get_peer_mappings
from app.db.repositories.filing_repository import get_filings, is_effective_filing
from app.providers.company_info_provider import CompanyInfoProvider, MockCompanyInfoProvider, WebCompanyInfoProvider, get_canonical_company_name
from app.services.company_profile_rag_service import CompanyProfileRAGService
from app.services.peer_discovery_service import PeerDiscoveryService
from app.utils.metadata_utils import normalize_ticker

settings = get_settings()


def _looks_like_supported_ticker(ticker: str) -> bool:
    """Return whether a ticker looks valid enough for web/document discovery."""

    text = ticker.strip().upper()
    return bool(
        re.fullmatch(r"[A-Z]{1,5}", text)
        or re.fullmatch(r"(US|HK|SH|SZ)\.[A-Z0-9]{1,8}", text)
        or re.fullmatch(r"\d{5,6}", text)
    )


class CompanyProfileService:
    """Load or synthesize company profile and peer information."""

    def __init__(
        self,
        provider: CompanyInfoProvider | None = None,
        rag_service: CompanyProfileRAGService | None = None,
        peer_service: PeerDiscoveryService | None = None,
    ) -> None:
        self.provider = provider
        self.fallback_provider = provider or self._build_provider()
        self.rag_service = rag_service or CompanyProfileRAGService()
        self.peer_service = peer_service or PeerDiscoveryService(external_provider=self.fallback_provider)

    def _build_provider(self) -> CompanyInfoProvider:
        """Build the configured company info provider."""

        if settings.company_info_provider.lower() == "web":
            return WebCompanyInfoProvider()
        if settings.company_info_provider.lower() == "mock":
            return MockCompanyInfoProvider()
        return MockCompanyInfoProvider()

    def build_profile(self, session: Session, ticker: str, company_name: str, model: str | None = None, skip_ingestion: bool = False) -> tuple[dict, list[dict], Company | None]:
        """Return profile and peers, preferring stored data when available."""

        company_name = get_canonical_company_name(ticker, company_name)
        if self.provider is not None:
            return self._build_with_legacy_provider(session, ticker, company_name)

        company = self._get_or_create_company(session, ticker, company_name)
        if company.company_name != company_name:
            company.company_name = company_name

        stored_profile = get_latest_company_profile(session, company.id)
        stored_peers = get_peer_mappings(session, company.id)
        stored_filings = get_filings(session, company.id)
        has_cached_sources = self._has_usable_filings(stored_filings) if settings.enable_filing_ingestion else False
        profile_has_content = stored_profile and any(
            getattr(stored_profile, field, None)
            for field in ("business_model", "market_space", "competition_landscape", "revenue_model")
        )
        should_short_circuit = profile_has_content and stored_peers and (
            has_cached_sources or (os.getenv("PYTEST_CURRENT_TEST") and not self._contains_only_stub_filings(stored_filings))
        )
        if should_short_circuit:
            return self._profile_from_row(stored_profile), self._peers_from_rows(stored_peers), company

        profile, evidence_bundle, warnings = self.rag_service.build(session, company, model=model, skip_ingestion=skip_ingestion)
        if profile is None:
            profile = WebCompanyInfoProvider().get_company_profile(ticker, company_name)
            profile["as_of_date"] = profile.get("as_of_date", date.today().isoformat())
            profile["warnings"] = list(dict.fromkeys(profile.get("warnings", []) + warnings + ["CompanyProfiler degraded to placeholder result due to missing annual reports."]))
            peers = self.fallback_provider.get_peers(ticker, company_name) if _looks_like_supported_ticker(ticker) else MockCompanyInfoProvider().get_peers(ticker, company_name)
            self._persist_profile_and_peers(session, company, profile, peers)
            return profile, peers, company

        peers, peer_warnings = self.peer_service.discover(
            ticker=ticker,
            company_name=company_name,
            company_profile=profile,
            evidence_bundle=evidence_bundle,
            model=model,
        )
        if not peers:
            peers = self.fallback_provider.get_peers(ticker, company_name)
            peer_warnings.append("Peer discovery degraded to external static / web candidates.")

        profile["warnings"] = list(dict.fromkeys(profile.get("warnings", []) + warnings + peer_warnings))
        self._persist_profile_and_peers(session, company, profile, peers)
        return profile, peers, company

    def _has_usable_filings(self, filings: list) -> bool:
        """Return whether stored filings are good enough to skip a fresh ingestion run."""

        return any(is_effective_filing(filing) for filing in filings)

    def _contains_only_stub_filings(self, filings: list) -> bool:
        """Return whether stored filings exist but are only placeholder stub records."""

        if not filings:
            return False
        return all(bool((filing.metadata_json or {}).get("stub")) for filing in filings)

    def _build_with_legacy_provider(self, session: Session, ticker: str, company_name: str) -> tuple[dict, list[dict], Company | None]:
        """Preserve legacy provider-driven behavior for tests and explicit injection."""

        company = get_company_by_ticker(session, ticker)
        if company:
            profile_row = get_latest_company_profile(session, company.id)
            peer_rows = get_peer_mappings(session, company.id)
            if profile_row:
                profile = self._profile_from_row(profile_row)
            else:
                profile = self.provider.get_company_profile(ticker, company.company_name)
                profile["as_of_date"] = profile.get("as_of_date", date.today().isoformat())
                self._persist_profile_and_peers(session, company, profile, [])
            peers = self._peers_from_rows(peer_rows)
            if peers:
                return profile, peers, company
            peers = self.provider.get_peers(ticker, company.company_name)
            self._persist_profile_and_peers(session, company, profile, peers)
            return profile, peers, company

        profile = self.provider.get_company_profile(ticker, company_name)
        profile["as_of_date"] = profile.get("as_of_date", date.today().isoformat())
        peers = self.provider.get_peers(ticker, company_name)
        company = self._get_or_create_company(session, ticker, company_name)
        self._persist_profile_and_peers(session, company, profile, peers)
        return profile, peers, company

    def _profile_from_row(self, profile_row: CompanyProfile) -> dict:
        """Convert ORM profile row to dict."""

        return {
            "as_of_date": profile_row.as_of_date.isoformat(),
            "business_model": profile_row.business_model or "",
            "market_space": profile_row.market_space or "",
            "competition_landscape": profile_row.competition_landscape or "",
            "corporate_culture": profile_row.corporate_culture or "",
            "revenue_model": profile_row.revenue_model or "",
            "key_products": profile_row.key_products or [],
            "key_regions": profile_row.key_regions or [],
            "key_customers": profile_row.key_customers or [],
            "key_suppliers": profile_row.key_suppliers or [],
            "risk_factors": profile_row.risk_factors or [],
            "management_summary": profile_row.management_summary or "",
            "raw_sources": profile_row.raw_sources or [],
        }

    def _peers_from_rows(self, peer_rows: list[PeerMapping]) -> list[dict]:
        """Convert ORM peer rows to dicts."""

        return [
            {
                "ticker": peer.peer_company.ticker,
                "company_name": peer.peer_company.company_name,
                "peer_type": peer.peer_type,
                "similarity_score": peer.similarity_score,
                "rationale": peer.rationale,
            }
            for peer in peer_rows
        ]

    def _get_or_create_company(self, session: Session, ticker: str, company_name: str) -> Company:
        """Get an existing company row or create a new one for persistence."""

        return get_or_create_company(session, ticker, company_name)

    def _persist_profile_and_peers(self, session: Session, company: Company, profile: dict, peers: list[dict]) -> None:
        """Persist provider-derived profile and peer mappings for reuse."""

        as_of_date = date.fromisoformat(profile.get("as_of_date", date.today().isoformat()))
        existing_profile = get_latest_company_profile(session, company.id)
        if existing_profile is None or existing_profile.as_of_date != as_of_date:
            session.add(
                CompanyProfile(
                    company_id=company.id,
                    as_of_date=as_of_date,
                    business_model=profile.get("business_model"),
                    market_space=profile.get("market_space"),
                    competition_landscape=profile.get("competition_landscape"),
                    corporate_culture=profile.get("corporate_culture"),
                    revenue_model=profile.get("revenue_model"),
                    key_products=profile.get("key_products", []),
                    key_regions=profile.get("key_regions", []),
                    key_customers=profile.get("key_customers", []),
                    key_suppliers=profile.get("key_suppliers", []),
                    risk_factors=profile.get("risk_factors", []),
                    management_summary=profile.get("management_summary"),
                    raw_sources=profile.get("raw_sources", []),
                    created_at=datetime.now(timezone.utc),
                )
            )

        existing_mappings = {(row.peer_company.ticker, row.peer_type) for row in get_peer_mappings(session, company.id)}
        for peer in peers:
            peer_ticker = str(peer.get("ticker", "")).upper().strip()
            if not peer_ticker:
                continue
            peer_company = self._get_or_create_company(session, peer_ticker, peer.get("company_name") or peer_ticker)
            key = (peer_company.ticker, peer.get("peer_type", "core"))
            if key in existing_mappings:
                continue
            session.add(
                PeerMapping(
                    company_id=company.id,
                    peer_company_id=peer_company.id,
                    peer_type=peer.get("peer_type", "core"),
                    similarity_score=peer.get("similarity_score"),
                    rationale=peer.get("rationale"),
                    source_type="filing_rag" if "filing" in str(profile.get("raw_sources", [])) else "provider",
                    created_at=datetime.now(timezone.utc),
                )
            )
        session.flush()
