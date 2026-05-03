"""Retrieval service built on top of the vector store abstraction."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.providers.vector_store_provider import VectorStoreProvider, build_default_vector_store
from app.services.embedding_service import EmbeddingService

logger = get_logger(__name__)


class RetrievalService:
    """Search indexed filing chunks through a provider-neutral interface."""

    def __init__(
        self,
        vector_store: VectorStoreProvider | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.vector_store = vector_store or build_default_vector_store()
        self.embedding_service = embedding_service or EmbeddingService()

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        """Search relevant filing chunks with optional metadata filters."""

        query_embedding = self.embedding_service.embed_texts([query])[0]
        payload = dict(filters or {})
        payload["__query_embedding__"] = query_embedding
        results = self.vector_store.search(query=query, filters=payload, top_k=top_k)
        logger.info("filing_retrieval_completed", query=query[:120], top_k=top_k, result_count=len(results))
        return results
