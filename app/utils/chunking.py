"""Chunking primitives for filing RAG ingestion."""

from __future__ import annotations

from math import ceil


def estimate_token_count(text: str) -> int:
    """Cheap token estimate suitable for tests and local ingestion."""

    return max(1, ceil(len(text) / 4))


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text by character length with overlap."""

    if chunk_size <= 0:
        return [text]
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        part = text[start:end].strip()
        if part:
            chunks.append(part)
        if end >= len(text):
            break
        start = max(0, end - max(0, overlap))
    return chunks
