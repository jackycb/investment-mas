"""Retrieval policy configuration — maps each analysis area to its source priority."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourcePriority:
    """Ordered list of sources for a given analysis area."""

    primary: list[str] = field(default_factory=list)
    secondary: list[str] = field(default_factory=list)
    evidence_only: list[str] = field(default_factory=list)


RETRIEVAL_POLICY: dict[str, SourcePriority] = {
    "company_profile": SourcePriority(
        primary=["filing_rag"],
        secondary=["web_provider"],
        evidence_only=[],
    ),
    "business_analysis": SourcePriority(
        primary=["filing_rag"],
        secondary=["web_search"],
        evidence_only=[],
    ),
    "culture_governance": SourcePriority(
        primary=["filing_rag", "structured_tables"],
        secondary=["news_events"],
        evidence_only=[],
    ),
    "peer_analysis": SourcePriority(
        primary=["peer_candidates", "peer_metrics", "sector_clustering"],
        secondary=["web_provider"],
        evidence_only=["filing_rag"],
    ),
    "financial_analysis": SourcePriority(
        primary=["structured_db", "local_financial_dir"],
        secondary=["database_fallback"],
        evidence_only=["filing_rag"],
    ),
    "valuation": SourcePriority(
        primary=["structured_db", "company_info"],
        secondary=["supplemental_script"],
        evidence_only=["filing_rag"],
    ),
    "risk_analysis": SourcePriority(
        primary=["filing_risk_factors", "structured_red_flags"],
        secondary=["news_events", "web_updates"],
        evidence_only=[],
    ),
    "followup_qa": SourcePriority(
        primary=["report_json", "filing_rag", "structured_db"],
        secondary=["web_search"],
        evidence_only=[],
    ),
}


def get_policy(area: str) -> SourcePriority:
    """Return the retrieval policy for a given analysis area."""

    return RETRIEVAL_POLICY.get(area, SourcePriority(primary=["filing_rag"], secondary=["web_search"]))
