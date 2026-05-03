"""Embedding service wrapper."""

from __future__ import annotations

from app.core.config import get_settings
from app.providers.embedding_provider import EmbeddingProvider, LocalHashEmbeddingProvider

settings = get_settings()


class EmbeddingService:
    """Resolve and call the configured embedding provider."""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self.provider = provider or self._build_provider()

    def _build_provider(self) -> EmbeddingProvider:
        if settings.default_embedding_provider.lower() == "local_hash":
            return LocalHashEmbeddingProvider()
        return LocalHashEmbeddingProvider()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""

        return self.provider.embed_texts(texts)
