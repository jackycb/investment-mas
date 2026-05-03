"""Chunking service for parsed filing text."""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.utils.chunking import estimate_token_count, split_text

settings = get_settings()


class ChunkingService:
    """Create filing chunks with overlap and metadata propagation."""

    def __init__(self, chunk_size: int | None = None, overlap: int | None = None) -> None:
        self.chunk_size = chunk_size or settings.default_chunk_size
        self.overlap = overlap or settings.default_chunk_overlap

    def build_chunks(self, parsed_records: list[dict[str, Any]], base_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert parsed filing records into chunk payloads."""

        chunks: list[dict[str, Any]] = []
        chunk_index = 0
        for record in parsed_records:
            for part in split_text(str(record.get("text") or ""), self.chunk_size, self.overlap):
                chunks.append(
                    {
                        "chunk_index": chunk_index,
                        "section": record.get("section"),
                        "page_number": record.get("page_number"),
                        "text": part,
                        "token_count": estimate_token_count(part),
                        "metadata_json": {
                            **base_metadata,
                            "section": record.get("section"),
                            "page_number": record.get("page_number"),
                        },
                    }
                )
                chunk_index += 1
        return chunks
