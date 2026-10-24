"""evidence_retrieval_skill — one memoized entry point for annual-report evidence.

Optimization task §4.4. The "vector search, then PostgreSQL keyword fallback"
pattern is currently duplicated in ``BusinessAnalysisService``,
``CultureGovernanceService``, ``RiskService``, ``CompanyProfileRAGService``,
``FilingCoverageService`` and ``FollowupQaService``. This skill unifies it
without changing any query semantics or the citation payload shape.

Guarantees:

  * **Same output shape** as the existing keyword fallback —
    ``id`` / ``text`` / ``score`` / ``filing_id`` / ``section`` / ``page_number``
    / ``metadata`` — so existing citation rendering keeps working.
  * **Lazy vector store.** The store is only touched when a semantic search is
    actually requested; with ``VECTOR_STORE_TYPE=none`` (the measured production
    setting) the skill goes straight to the SQL path.
  * **Per-run memoization.** Identical ``(company_id, query, top_k, keywords)``
    lookups execute once, so parallel specialists no longer repeat the same
    retrieval.
  * No new embedding model and no reranker (§4.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import FilingChunk
from app.services.compute_cache_service import CACHE_METRICS, cache_key, cached_compute
from app.services.retrieval_service import RetrievalService
from app.skills.base import skill_span

logger = get_logger(__name__)


@dataclass
class EvidenceResult:
    """Retrieved evidence plus the provenance needed for citations."""

    area: str
    company_id: int | None
    hits: list[dict[str, Any]] = field(default_factory=list)
    source: str = "none"  # vector | keyword | none
    cache_hit: bool = False
    queries: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.hits)

    def citations(self) -> list[dict[str, Any]]:
        """Compact citation records (document / section / page / chunk id)."""

        out: list[dict[str, Any]] = []
        for hit in self.hits:
            metadata = hit.get("metadata") or {}
            out.append(
                {
                    "chunk_id": hit.get("id"),
                    "filing_id": hit.get("filing_id"),
                    "section": hit.get("section"),
                    "page_number": hit.get("page_number"),
                    "fiscal_year": metadata.get("fiscal_year"),
                    "filing_type": metadata.get("filing_type"),
                    "ticker": metadata.get("ticker"),
                    "score": hit.get("score"),
                    "source": self.source,
                }
            )
        return out


class EvidenceRetrievalSkill:
    """Shared, memoized evidence lookup over the existing retrieval services."""

    def __init__(self, retrieval_service: RetrievalService | None = None) -> None:
        # Constructed lazily: RetrievalService.__init__ resolves the vector
        # store, which must not happen for purely structured analyses.
        self._retrieval_service = retrieval_service

    @property
    def retrieval_service(self) -> RetrievalService:
        if self._retrieval_service is None:
            self._retrieval_service = RetrievalService()
        return self._retrieval_service

    def retrieve(
        self,
        session: Session,
        company_id: int,
        *,
        area: str = "general",
        queries: list[str] | None = None,
        keywords: list[str] | None = None,
        top_k: int = 4,
        filing_type: str | None = "annual_report",
        max_hits: int = 8,
        use_vector: bool = True,
        force_refresh: bool = False,
    ) -> EvidenceResult:
        """Return evidence for one analysis area.

        ``use_vector=False`` skips semantic search entirely — the path a
        structured-only analysis should take so it is never blocked by the
        vector store (§9).
        """

        query_list = [q for q in (queries or []) if q]
        keyword_list = [k for k in (keywords or []) if k]
        if not query_list and not keyword_list:
            return EvidenceResult(area=area, company_id=company_id, source="none")

        key = cache_key(
            "evidence", company_id, area, tuple(query_list), tuple(sorted(keyword_list)), top_k, filing_type, use_vector
        )
        with skill_span("evidence_retrieval") as record:
            payload, hit = cached_compute(
                CACHE_METRICS,
                key,
                lambda: self._retrieve_uncached(
                    session,
                    company_id,
                    query_list,
                    keyword_list,
                    top_k=top_k,
                    filing_type=filing_type,
                    max_hits=max_hits,
                    use_vector=use_vector,
                ),
                force_refresh=force_refresh,
                # An empty result may be transient (index still building); never cache it.
                cache_ok=lambda value: bool(value.get("hits")),
            )
            record.cache_hit = hit
            record.skill_name = f"evidence_retrieval:{area}"
        return EvidenceResult(
            area=area,
            company_id=company_id,
            hits=list(payload.get("hits") or []),
            source=payload.get("source", "none"),
            cache_hit=hit,
            queries=list(query_list),
        )


    def _retrieve_uncached(
        self,
        session: Session,
        company_id: int,
        queries: list[str],
        keywords: list[str],
        *,
        top_k: int,
        filing_type: str | None,
        max_hits: int,
        use_vector: bool,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        source = "none"

        if use_vector and queries:
            filters: dict[str, Any] = {"company_id": company_id}
            if filing_type:
                filters["filing_type"] = filing_type
            for query in queries:
                try:
                    hits = self.retrieval_service.search(query=query, filters=filters, top_k=top_k)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("evidence_vector_search_failed", company_id=company_id, error=str(exc)[:160])
                    hits = []
                for hit in hits:
                    hit_id = f"{hit.get('filing_id')}:{hit.get('section')}"
                    if hit_id not in seen:
                        seen.add(hit_id)
                        results.append(hit)
            if results:
                source = "vector"

        if not results and keywords:
            results = self.keyword_search(session, company_id, keywords, top_k=top_k * 2, filing_type=filing_type)
            if results:
                source = "keyword"

        return {"hits": results[:max_hits], "source": source}

    @staticmethod
    def keyword_search(
        session: Session,
        company_id: int,
        keywords: list[str],
        *,
        top_k: int = 8,
        filing_type: str | None = "annual_report",
    ) -> list[dict[str, Any]]:
        """PostgreSQL keyword search over filing chunks (existing semantics)."""

        if not keywords:
            return []
        conditions = [func.lower(FilingChunk.text).contains(keyword.lower()) for keyword in keywords]
        stmt = (
            select(FilingChunk)
            .where(FilingChunk.company_id == company_id)
            .where(or_(*conditions))
            .limit(top_k)
        )
        rows = session.scalars(stmt).all()
        results: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.metadata_json or {}
            if filing_type and metadata.get("filing_type") not in {None, filing_type}:
                continue
            score = sum(1 for keyword in keywords if keyword.lower() in f"{row.section or ''} {row.text}".lower())
            results.append(
                {
                    "id": f"{row.filing_id}:{row.chunk_index}",
                    "text": row.text,
                    "score": float(score or 1),
                    "filing_id": row.filing_id,
                    "section": row.section,
                    "page_number": row.page_number,
                    "metadata": metadata,
                }
            )
        return results


_default_evidence_skill: EvidenceRetrievalSkill | None = None


def get_evidence_retrieval_skill() -> EvidenceRetrievalSkill:
    """Return the process-wide evidence skill (memoization is shared per run)."""

    global _default_evidence_skill
    if _default_evidence_skill is None:
        _default_evidence_skill = EvidenceRetrievalSkill()
    return _default_evidence_skill


def retrieve_evidence(
    session: Session,
    company_id: int,
    *,
    area: str = "general",
    queries: list[str] | None = None,
    keywords: list[str] | None = None,
    top_k: int = 4,
    filing_type: str | None = "annual_report",
    use_vector: bool = True,
    force_refresh: bool = False,
) -> EvidenceResult:
    """Functional entry point for the evidence retrieval skill."""

    return get_evidence_retrieval_skill().retrieve(
        session,
        company_id,
        area=area,
        queries=queries,
        keywords=keywords,
        top_k=top_k,
        filing_type=filing_type,
        use_vector=use_vector,
        force_refresh=force_refresh,
    )

