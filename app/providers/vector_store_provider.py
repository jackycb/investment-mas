"""Vector store provider abstractions for filing retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.embedding_provider import LocalHashEmbeddingProvider

logger = get_logger(__name__)
settings = get_settings()

_hash_provider = LocalHashEmbeddingProvider()


class VectorStoreProvider(ABC):
    """Abstract vector store provider."""

    @abstractmethod
    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Persist filing chunks and embeddings into the vector store."""

    @abstractmethod
    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        """Search relevant chunks using a query and optional metadata filters."""


class NullVectorStoreProvider(VectorStoreProvider):
    """No-op vector store used when Chroma is unavailable locally."""

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        if chunks:
            logger.warning("vector_store_unavailable_upsert_skipped", chunk_count=len(chunks))

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        logger.warning("vector_store_unavailable_search_skipped", query=query[:120], top_k=top_k)
        return []


class ChromaVectorStoreProvider(VectorStoreProvider):
    """Persistent Chroma-backed vector store.

    Always passes explicit embeddings to avoid ChromaDB invoking its
    built-in default embedding function (all-MiniLM-L6-v2, 384-dim)
    which is incompatible with the 64-dim hash embeddings stored in
    the collection.
    """

    def __init__(self, collection_name: str = "filing_chunks_local_hash_64") -> None:
        try:
            import chromadb
        except ModuleNotFoundError as exc:
            raise RuntimeError("chromadb_unavailable") from exc

        self.collection_name = collection_name
        self.client = chromadb.PersistentClient(path=settings.vector_store_dir)
        self.collection = self.client.get_or_create_collection(collection_name)

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        try:
            self._upsert(chunks)
        except Exception as exc:
            if "dimension" not in str(exc).lower():
                raise
            logger.warning(
                "chroma_collection_dimension_mismatch_rebuild",
                collection=self.collection_name,
                error=str(exc),
            )
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.get_or_create_collection(self.collection_name)
            self._upsert(chunks)
        logger.info("vector_chunks_upserted", provider="chroma", chunk_count=len(chunks), collection=self.collection_name)

    def _upsert(self, chunks: list[dict[str, Any]]) -> None:
        """Upsert chunks into Chroma with explicit embeddings."""

        self.collection.upsert(
            ids=[str(item["vector_id"]) for item in chunks],
            documents=[item["text"] for item in chunks],
            embeddings=[item["embedding"] for item in chunks],
            metadatas=[item["metadata"] for item in chunks],
        )

    @staticmethod
    def _build_where(filters: dict[str, Any] | None) -> dict[str, Any] | None:
        """Convert a flat filter dict into ChromaDB ``$and`` format when needed."""
        if not filters:
            return None
        plain = {k: v for k, v in filters.items() if not k.startswith("_")}
        if len(plain) <= 1:
            return plain or None
        return {"$and": [{k: v} for k, v in plain.items()]}

    def search(self, query: str, filters: dict[str, Any] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        query_embedding = filters.pop("__query_embedding__", None) if filters else None
        if query_embedding is None:
            query_embedding = _hash_provider.embed_texts([query])[0]
        where = self._build_where(filters)
        try:
            payload = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where,
            )
        except Exception as exc:
            logger.warning("chroma_query_failed", error=str(exc), where=where, top_k=top_k)
            return []
        documents = payload.get("documents", [[]])[0]
        metadatas = payload.get("metadatas", [[]])[0]
        distances = payload.get("distances", [[]])[0]
        ids = payload.get("ids", [[]])[0]
        results: list[dict[str, Any]] = []
        for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            results.append(
                {
                    "id": chunk_id,
                    "text": text,
                    "score": 1 - float(distance or 0.0),
                    "filing_id": metadata.get("filing_id"),
                    "section": metadata.get("section"),
                    "page_number": metadata.get("page_number"),
                    "metadata": metadata,
                }
            )
        if results:
            best = max(r["score"] for r in results)
            logger.debug("chroma_search_done", count=len(results), best_score=round(best, 4), where=where)
        return results


def build_default_vector_store() -> VectorStoreProvider:
    """Build the configured vector store.

    Runtime filing ingestion must not silently drop vectors. Use
    ``VECTOR_STORE_TYPE=none`` explicitly in tests/dev experiments if a no-op
    provider is desired.
    """

    store_type = settings.vector_store_type.lower()
    if store_type == "none":
        return NullVectorStoreProvider()
    if store_type == "chroma":
        try:
            return ChromaVectorStoreProvider()
        except Exception as exc:
            logger.error("vector_store_provider_init_failed", provider=settings.vector_store_type, error=str(exc))
            raise RuntimeError(f"Vector store initialization failed: {exc}") from exc
    raise ValueError(f"Unsupported VECTOR_STORE_TYPE: {settings.vector_store_type}")
