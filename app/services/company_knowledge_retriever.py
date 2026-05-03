"""Retrieve company knowledge bundles from parsed document chunks."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.document_repository import get_document_chunks

logger = get_logger(__name__)
settings = get_settings()


TOPIC_QUERIES: dict[str, list[str]] = {
    "business_model": ["business", "overview", "how we operate", "主营业务", "业务模式"],
    "revenue_model": ["revenue", "monetize", "收入", "收费", "pricing"],
    "key_products": ["products", "services", "solution", "产品", "服务"],
    "key_customers": ["customers", "client", "merchant", "客户", "用户"],
    "key_regions": ["geographic", "region", "china", "international", "地区", "区域"],
    "competition": ["competition", "competitive", "market share", "competitor", "竞争", "同业"],
    "management": ["management", "leadership", "board", "治理", "管理层"],
    "risk_factors": ["risk factors", "risks", "uncertainty", "风险因素", "主要风险"],
}


class CompanyKnowledgeRetriever:
    """Retrieve evidence bundles for company profiling topics."""

    def __init__(self, vector_backend: str | None = None, index_dir: str | None = None) -> None:
        self.vector_backend = (vector_backend or settings.company_profile_vector_backend).lower()
        self.index_dir = index_dir or settings.company_profile_index_dir

    def retrieve(self, session: Session, company_id: int, topics: list[str], top_k: int = 4) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        """Retrieve evidence chunks for each requested topic."""

        chunks = get_document_chunks(session, company_id)
        warnings: list[str] = []
        if not chunks:
            return {topic: [] for topic in topics}, ["No retrievable document chunks found; unable to build evidence bundle."]

        if self.vector_backend == "chroma":
            try:
                return self._retrieve_with_chroma(chunks, topics, top_k), warnings
            except Exception as exc:
                warnings.append(f"Local Chroma retrieval unavailable, falling back to keyword search: {exc}")
                logger.info("company_knowledge_retriever_chroma_fallback", error=str(exc))

        return self._retrieve_lexically(chunks, topics, top_k), warnings

    def _retrieve_with_chroma(self, chunks: list[Any], topics: list[str], top_k: int) -> dict[str, list[dict[str, Any]]]:
        """Retrieve with local Chroma when dependency is available."""

        import chromadb

        client = chromadb.PersistentClient(path=self.index_dir)
        collection = client.get_or_create_collection("company_profiler_chunks")
        ids = [f"{chunk.company_document_id}:{chunk.chunk_index}" for chunk in chunks]
        existing = set(collection.get(ids=ids).get("ids", []))
        missing = [chunk for chunk, chunk_id in zip(chunks, ids, strict=False) if chunk_id not in existing]
        if missing:
            collection.add(
                ids=[f"{chunk.company_document_id}:{chunk.chunk_index}" for chunk in missing],
                documents=[chunk.text for chunk in missing],
                metadatas=[
                    {
                        "company_id": chunk.company_id,
                        "ticker": chunk.ticker,
                        "doc_type": chunk.doc_type,
                        "section": chunk.section or "",
                        "fiscal_year": chunk.fiscal_year or 0,
                        "page_number": chunk.page_number or 0,
                    }
                    for chunk in missing
                ],
            )

        bundle: dict[str, list[dict[str, Any]]] = {}
        for topic in topics:
            query = " ".join(TOPIC_QUERIES.get(topic, [topic]))
            result = collection.query(
                query_texts=[query],
                n_results=top_k,
                where={"company_id": chunks[0].company_id},
            )
            docs = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            bundle[topic] = [
                {
                    "text": text,
                    "section": meta.get("section"),
                    "doc_type": meta.get("doc_type"),
                    "fiscal_year": meta.get("fiscal_year") or None,
                    "page_number": meta.get("page_number") or None,
                }
                for text, meta in zip(docs, metas, strict=False)
            ]
        return bundle

    def _retrieve_lexically(self, chunks: list[Any], topics: list[str], top_k: int) -> dict[str, list[dict[str, Any]]]:
        """Simple keyword-based retrieval fallback."""

        bundle: dict[str, list[dict[str, Any]]] = {}
        for topic in topics:
            keywords = [keyword.lower() for keyword in TOPIC_QUERIES.get(topic, [topic])]
            scored: list[tuple[float, Any]] = []
            for chunk in chunks:
                haystack = f"{chunk.section or ''}\n{chunk.text}".lower()
                score = 0.0
                for keyword in keywords:
                    if keyword in haystack:
                        score += 1.0
                if topic == "competition" and chunk.doc_type in {"annual_report", "20-f", "10-k"}:
                    score += 0.5
                if score > 0:
                    scored.append((score, chunk))
            scored.sort(key=lambda item: (item[0], item[1].fiscal_year or 0, item[1].page_number or 0), reverse=True)
            bundle[topic] = [
                {
                    "text": chunk.text,
                    "section": chunk.section,
                    "doc_type": chunk.doc_type,
                    "fiscal_year": chunk.fiscal_year,
                    "page_number": chunk.page_number,
                }
                for _, chunk in scored[:top_k]
            ]
        return bundle

    def summarize_sources(self, evidence_bundle: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Build a compact source summary for raw_sources."""

        summary: list[dict[str, Any]] = []
        counter: dict[tuple[str, Any], set[str]] = defaultdict(set)
        for topic, items in evidence_bundle.items():
            for item in items:
                counter[(item.get("doc_type", "other"), item.get("fiscal_year"))].add(topic)
        for (doc_type, fiscal_year), topics in counter.items():
            summary.append({"source": "document_rag", "doc_type": doc_type, "fiscal_year": fiscal_year, "topics": sorted(topics)})
        return summary
