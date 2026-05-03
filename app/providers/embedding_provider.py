"""Embedding provider abstractions for filing chunks."""

from __future__ import annotations

from abc import ABC, abstractmethod
import math

from app.core.logging import get_logger
from app.utils.hashing import sha256_text

logger = get_logger(__name__)


class EmbeddingProvider(ABC):
    """Abstract text embedding provider."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""


class LocalHashEmbeddingProvider(EmbeddingProvider):
    """Deterministic local embedding based on hashing, good enough for local MVP."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            seed = sha256_text(text)
            values = [0.0] * self.dimensions
            for index in range(self.dimensions):
                pair = seed[(index * 2) % len(seed) : ((index * 2) % len(seed)) + 2]
                if len(pair) < 2:
                    pair = (pair + seed)[:2]
                values[index] = (int(pair, 16) / 255.0) - 0.5
            norm = math.sqrt(sum(item * item for item in values)) or 1.0
            vectors.append([item / norm for item in values])
        return vectors
