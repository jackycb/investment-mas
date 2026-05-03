"""Peer discovery service based on document evidence plus external candidates."""

from __future__ import annotations
from typing import Any

from app.core.logging import get_logger
from app.providers.company_info_provider import CURATED_PEERS, CompanyInfoProvider, WebCompanyInfoProvider
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service

logger = get_logger(__name__)


class PeerDiscoveryService:
    """Discover and explain peer candidates using competition evidence first."""

    def __init__(
        self,
        external_provider: CompanyInfoProvider | None = None,
        llm_service: AliyunLLMService | None = None,
    ) -> None:
        self.external_provider = external_provider or WebCompanyInfoProvider()
        self.llm_service = llm_service or get_llm_service()

    def discover(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        evidence_bundle: dict[str, list[dict[str, Any]]],
        model: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return peer candidates and warnings."""

        warnings: list[str] = []
        candidates = self._from_competition_evidence(ticker, evidence_bundle.get("competition", []))
        external_candidates = self.external_provider.get_peers(ticker, company_name)
        if not candidates:
            if external_candidates:
                warnings.append("No peers directly identified from annual report / IR competition sections; supplemented with industry / static candidates.")
            else:
                warnings.append("Could not identify clear peers from annual report / IR competition sections; combined with external candidates.")
        merged = self._merge_candidates(candidates, external_candidates)
        if not merged:
            return [], warnings + ["No usable peer candidates could be identified."]

        if self.llm_service.available:
            payload = self.llm_service.annotate_peer_candidates(
                ticker=ticker,
                company_name=company_name,
                candidates=merged,
                model=model,
                raw_context={
                    "company_profile": company_profile,
                    "competition_evidence": evidence_bundle.get("competition", []),
                },
            )
            if payload:
                return self._merge_llm_annotations(merged, payload), warnings
            warnings.append("LLM peer classification explanation failed validation; fell back to rule-based explanation.")
        return self._attach_rule_based_rationale(merged), warnings

    def _from_competition_evidence(self, ticker: str, competition_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Infer peer candidates from competition evidence snippets."""

        alias_map: dict[str, dict[str, Any]] = {}
        for _, peers in CURATED_PEERS.items():
            for peer in peers:
                alias_map[str(peer.get("company_name", "")).lower()] = peer
                alias_map[str(peer.get("ticker", "")).lower()] = peer

        found: list[dict[str, Any]] = []
        for chunk in competition_chunks:
            text = str(chunk.get("text", ""))
            lowered = text.lower()
            for alias, peer in alias_map.items():
                if alias and alias in lowered and str(peer.get("ticker", "")).upper() != ticker.upper():
                    found.append(
                        {
                            "ticker": peer.get("ticker"),
                            "company_name": peer.get("company_name"),
                            "peer_type": peer.get("peer_type", "core"),
                            "similarity_score": max(float(peer.get("similarity_score", 0.65)), 0.65),
                            "similarity_dimensions": ["industry", "business_model"],
                            "rationale": f"{peer.get('company_name')} appeared in competition section document evidence and may serve as a peer candidate.",
                        }
                    )
        return found

    def _merge_candidates(self, first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge peer candidates by ticker."""

        merged: dict[str, dict[str, Any]] = {}
        for item in first + second:
            ticker = str(item.get("ticker", "")).upper().strip()
            if not ticker:
                continue
            if ticker not in merged:
                merged[ticker] = {
                    "ticker": ticker,
                    "company_name": item.get("company_name") or ticker,
                    "peer_type": item.get("peer_type", "secondary"),
                    "similarity_score": item.get("similarity_score", 0.6),
                    "similarity_dimensions": item.get("similarity_dimensions", []),
                    "rationale": item.get("rationale"),
                }
                continue
            merged[ticker]["similarity_score"] = max(
                float(merged[ticker].get("similarity_score") or 0.0),
                float(item.get("similarity_score") or 0.0),
            )
            merged[ticker]["similarity_dimensions"] = sorted(
                set(merged[ticker].get("similarity_dimensions", [])) | set(item.get("similarity_dimensions", []))
            )
            if not merged[ticker].get("rationale") and item.get("rationale"):
                merged[ticker]["rationale"] = item.get("rationale")
        return list(merged.values())[:6]

    def _merge_llm_annotations(self, base_candidates: list[dict[str, Any]], payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Combine validated LLM annotations with deterministic candidates."""

        base_map = {str(item.get("ticker", "")).upper(): item for item in base_candidates}
        merged: list[dict[str, Any]] = []
        for item in payload:
            ticker = str(item.get("ticker", "")).upper()
            base = base_map.get(ticker)
            if not base:
                continue
            merged.append(
                {
                    **base,
                    "peer_type": item.get("peer_type") or base.get("peer_type"),
                    "similarity_dimensions": item.get("similarity_dimensions") or base.get("similarity_dimensions", []),
                    "similarity_score": item.get("confidence") or base.get("similarity_score"),
                    "rationale": item.get("rationale") or base.get("rationale"),
                }
            )
        return merged or base_candidates

    def _attach_rule_based_rationale(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach deterministic rationale if LLM annotation is unavailable."""

        return [
            {
                **candidate,
                "rationale": candidate.get("rationale")
                or f"{candidate.get('company_name')} is comparable to the target company in industry or business model and may serve as a {candidate.get('peer_type', 'secondary')} peer candidate.",
            }
            for candidate in candidates
        ]
