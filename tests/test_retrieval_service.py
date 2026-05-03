"""Tests for retrieval service using a fake vector store."""

from __future__ import annotations

from unittest.mock import patch

from app.providers.embedding_provider import LocalHashEmbeddingProvider
from app.providers.vector_store_provider import NullVectorStoreProvider
from app.services.embedding_service import EmbeddingService
from app.services.retrieval_service import RetrievalService


class _FakeVectorStore:
    def __init__(self) -> None:
        self.chunks: list[dict] = []

    def upsert_chunks(self, chunks: list[dict]) -> None:
        self.chunks.extend(chunks)

    def search(self, query: str, filters: dict | None = None, top_k: int = 5) -> list[dict]:
        target_company = (filters or {}).get("company_id")
        items = [item for item in self.chunks if item["metadata"].get("company_id") == target_company]
        return [
            {
                "text": item["text"],
                "score": 0.9,
                "filing_id": item["metadata"].get("filing_id"),
                "section": item["metadata"].get("section"),
                "page_number": item["metadata"].get("page_number"),
                "metadata": item["metadata"],
            }
            for item in items[:top_k]
        ]


def test_retrieval_service_returns_filtered_results() -> None:
    store = _FakeVectorStore()
    embedder = EmbeddingService(provider=LocalHashEmbeddingProvider())
    vectors = embedder.embed_texts(["核心业务包括电商平台和跨境业务。"])
    store.upsert_chunks(
        [
            {
                "vector_id": "1:0",
                "text": "核心业务包括电商平台和跨境业务。",
                "embedding": vectors[0],
                "metadata": {"company_id": 7, "filing_id": 1, "section": "Business", "page_number": 3},
            }
        ]
    )
    service = RetrievalService(vector_store=store, embedding_service=embedder)

    result = service.search("检索业务模式", filters={"company_id": 7}, top_k=3)

    assert len(result) == 1
    assert result[0]["filing_id"] == 1
    assert result[0]["section"] == "Business"


def test_retrieval_service_falls_back_when_chroma_unavailable() -> None:
    with patch("app.services.retrieval_service.build_default_vector_store", return_value=NullVectorStoreProvider()):
        service = RetrievalService(embedding_service=EmbeddingService(provider=LocalHashEmbeddingProvider()))

    result = service.search("检索业务模式", filters={"company_id": 7}, top_k=3)

    assert result == []
