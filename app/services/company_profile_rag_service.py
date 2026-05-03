"""RAG-style company profile builder backed by filing ingestion and retrieval."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Company, FilingChunk
from app.db.repositories.filing_repository import get_filing_chunks, get_filings
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service
from app.services.filing_service import FilingService
from app.services.retrieval_service import RetrievalService
from app.utils.metadata_utils import infer_market_from_ticker, json_safe

logger = get_logger(__name__)

TOPIC_QUERIES: dict[str, str] = {
    "business_model": "business, overview, operations, core business segments, products and services",
    "revenue_model": "revenue sources, monetization, how the company generates income",
    "key_products": "key products, services, platforms, solutions, GPU, data center, cloud",
    "key_customers": "major customers, end users, enterprise clients, OEM partners",
    "key_regions": "geographic regions, international markets, revenue by country",
    "competition": "competitive landscape, competitors, market share, competitive advantages",
    "management": "management team, board of directors, corporate governance, executive officers",
    "corporate_culture": "corporate culture, values, mission, employee programs",
    "risk_factors": "risk factors, uncertainties, regulatory risks, litigation, export controls",
}

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "business_model": ["主营", "业务", "business", "model", "platform", "生态", "overview", "operations", "segment"],
    "revenue_model": ["收入", "revenue", "monetization", "commission", "advertising", "订阅", "net income", "gross profit"],
    "key_products": ["产品", "service", "platform", "solution", "app", "cloud", "data center", "gpu", "gaming", "automotive", "geforce", "drive"],
    "key_customers": ["客户", "merchant", "consumer", "user", "enterprise", "brand", "hyperscale", "oem", "partner"],
    "key_regions": ["china", "international", "overseas", "region", "地区", "海外", "united states", "taiwan", "geographic"],
    "competition": ["竞争", "compet", "peer", "market share", "rival", "行业", "amd", "intel", "industry"],
    "management": ["management", "董事", "ceo", "executive", "governance", "board", "officer", "director", "compensation"],
    "corporate_culture": ["culture", "价值观", "使命", "原则", "value", "mission", "employee", "talent", "innovation"],
    "risk_factors": ["risk", "风险", "uncertainty", "regulation", "litigation", "compliance", "geopolit", "supply chain", "export control"],
}


class CompanyProfileRAGService:
    """Ingest filings and synthesize a company profile from retrieved evidence."""

    def __init__(
        self,
        filing_service: FilingService | None = None,
        retrieval_service: RetrievalService | None = None,
        llm_service: AliyunLLMService | None = None,
    ) -> None:
        self.filing_service = filing_service or FilingService()
        self.retrieval_service = retrieval_service
        self.llm_service = llm_service or get_llm_service()

    def build(
        self,
        session: Session,
        company: Company,
        model: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, list[dict[str, Any]]], list[str]]:
        """Ingest filings if needed, retrieve topic evidence, and synthesize profile."""

        warnings: list[str] = []
        try:
            filings, ingestion_warnings = self.filing_service.ingest_company_filings(
                session,
                company,
                market=infer_market_from_ticker(company.ticker),
                filing_types={"annual_report"},
            )
        except TypeError:
            filings, ingestion_warnings = self.filing_service.ingest_company_filings(
                session,
                company,
                market=infer_market_from_ticker(company.ticker),
            )
        evidence_bundle = self._retrieve_evidence_bundle(session, company)
        if any(evidence_bundle.values()):
            ingestion_warnings = [
                warning
                for warning in ingestion_warnings
                if "Filing parsing failed or no text extracted" not in str(warning)
            ]
        warnings.extend(ingestion_warnings)

        if not any(evidence_bundle.values()):
            if not filings:
                warnings.append("No annual report filing evidence found for profile generation.")
            warnings.append("No retrievable annual report document chunks found; unable to build evidence bundle.")
            return None, evidence_bundle, warnings

        profile = self._synthesize_profile(company, evidence_bundle, model=model)
        if profile is None:
            warnings.append("Filing RAG evidence available, but LLM profile generation failed; falling back to rule-based summary.")
            profile = self._fallback_profile(company, evidence_bundle)

        profile["as_of_date"] = date.today().isoformat()
        profile["raw_sources"] = [
            *self._filing_source_summaries(filings),
            *self._evidence_source_summaries(evidence_bundle),
        ]
        profile["evidence_refs"] = {
            topic: [
                {
                    "filing_id": item.get("filing_id"),
                    "section": item.get("section"),
                    "page_number": item.get("page_number"),
                    "score": item.get("score"),
                }
                for item in items
            ]
            for topic, items in evidence_bundle.items()
        }
        profile["warnings"] = warnings
        return profile, evidence_bundle, warnings

    def _retrieve_evidence_bundle(self, session: Session, company: Company, top_k: int = 4) -> dict[str, list[dict[str, Any]]]:
        """Retrieve topic evidence from indexed filing chunks."""

        bundle: dict[str, list[dict[str, Any]]] = {}
        if self.retrieval_service is None:
            self.retrieval_service = RetrievalService()
        for topic, query in TOPIC_QUERIES.items():
            bundle[topic] = self.retrieval_service.search(
                query=query,
                filters={"company_id": company.id, "filing_type": "annual_report"},
                top_k=top_k,
            )
        has_vector = any(bundle.values())
        best_score = max(
            (float(h.get("score", 0)) for hits in bundle.values() for h in hits),
            default=0.0,
        )
        if has_vector and best_score >= 0.05:
            logger.info("evidence_bundle_from_vector", ticker=company.ticker, best_score=round(best_score, 4))
            return bundle
        logger.info("evidence_bundle_fallback_to_keywords", ticker=company.ticker, best_score=round(best_score, 4))
        return self._retrieve_evidence_bundle_from_chunks(session, company, top_k=top_k)

    def _retrieve_evidence_bundle_from_chunks(
        self,
        session: Session,
        company: Company,
        top_k: int = 4,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fallback retrieval over persisted filing chunks when vector search is unavailable."""

        chunks = get_filing_chunks(session, company.id)
        if not chunks:
            chunks = list(session.scalars(select(FilingChunk).where(FilingChunk.company_id == company.id)).all())
        bundle: dict[str, list[dict[str, Any]]] = {}
        annual_chunks = [c for c in chunks if (c.metadata_json or {}).get("filing_type") == "annual_report"]
        if not annual_chunks:
            annual_chunks = chunks
            logger.info("keyword_fallback_no_annual_filter", ticker=company.ticker, total_chunks=len(chunks))
        for topic, keywords in TOPIC_KEYWORDS.items():
            scored: list[tuple[int, Any]] = []
            for chunk in annual_chunks:
                haystack = f"{chunk.section or ''} {chunk.text}".lower()
                score = sum(1 for keyword in keywords if keyword.lower() in haystack)
                if score <= 0:
                    continue
                scored.append((score, chunk))
            scored.sort(key=lambda item: item[0], reverse=True)
            bundle[topic] = [
                {
                    "id": f"{chunk.filing_id}:{chunk.chunk_index}",
                    "text": chunk.text,
                    "score": float(score),
                    "filing_id": chunk.filing_id,
                    "section": chunk.section,
                    "page_number": chunk.page_number,
                    "metadata": chunk.metadata_json or {},
                }
                for score, chunk in scored[:top_k]
            ]
        return bundle

    def _synthesize_profile(self, company: Company, evidence_bundle: dict[str, list[dict[str, Any]]], model: str | None = None) -> dict[str, Any] | None:
        """Call the LLM with retrieved evidence bundles."""

        if not self.llm_service.available:
            return None
        try:
            return self.llm_service.synthesize_company_profile(
                ticker=company.ticker,
                company_name=company.company_name,
                raw_context={
                    "evidence_bundle": evidence_bundle,
                    "instruction": "Generate the company profile based solely on the downloaded annual_report document evidence. Do not fabricate facts.",
                },
                model=model,
            )
        except Exception as exc:
            logger.warning("company_profile_llm_synthesis_failed", ticker=company.ticker, error=str(exc))
            return None

    def _fallback_profile(self, company: Company, evidence_bundle: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        """Deterministic fallback summarizer for local runs without LLM."""

        def text(topic: str) -> str:
            items = evidence_bundle.get(topic, [])
            if not items:
                return "Insufficient information"
            return " ".join(str(item.get("text", ""))[:200] for item in items[:2]).strip() or "Insufficient information"

        def values(topic: str) -> list[str]:
            items = evidence_bundle.get(topic, [])
            return [str(item.get("text", ""))[:80] for item in items[:4] if str(item.get("text", "")).strip()]

        return {
            "company_name": company.company_name,
            "ticker": company.ticker,
            "business_model": text("business_model"),
            "revenue_model": text("revenue_model"),
            "key_products": values("key_products"),
            "key_customers": values("key_customers"),
            "key_regions": values("key_regions"),
            "market_space": text("business_model"),
            "competition_landscape": text("competition"),
            "competitive_advantages": [],
            "competitive_weaknesses": [],
            "risk_factors": values("risk_factors"),
            "corporate_culture": text("corporate_culture"),
            "management_summary": text("management"),
            "confidence": 0.45,
        }

    def _filing_source_summaries(self, filings: list[Any]) -> list[dict[str, Any]]:
        """Summarize ingested filings for raw_sources."""

        return [
            {
                "source": "filing",
                "filing_id": item.id,
                "filing_type": item.filing_type,
                "fiscal_year": item.fiscal_year,
                "source_name": item.source_name,
                "file_url": item.file_url,
            }
            for item in filings[:10]
            if item.filing_type == "annual_report"
        ]

    def _evidence_source_summaries(self, evidence_bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Summarize retrieved evidence usage."""

        summary: list[dict[str, Any]] = []
        seen: set[tuple[Any, str]] = set()
        for topic, items in evidence_bundle.items():
            for item in items:
                key = (item.get("filing_id"), topic)
                if key in seen:
                    continue
                seen.add(key)
                summary.append(
                    {
                        "source": "filing_rag",
                        "topic": topic,
                        "filing_id": item.get("filing_id"),
                        "section": item.get("section"),
                        "fiscal_year": item.get("metadata", {}).get("fiscal_year") if isinstance(item.get("metadata"), dict) else None,
                    }
                )
        return summary

    def _json_safe(self, value: Any) -> Any:
        """Compatibility wrapper kept for existing tests."""

        return json_safe(value)
