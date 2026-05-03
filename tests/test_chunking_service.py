"""Tests for filing chunking service."""

from __future__ import annotations

from app.services.chunking_service import ChunkingService


def test_chunking_service_builds_chunks_with_metadata() -> None:
    service = ChunkingService(chunk_size=20, overlap=5)

    chunks = service.build_chunks(
        [{"section": "Risk Factors", "page_number": 12, "text": "A" * 45}],
        {"ticker": "PDD", "market": "US", "filing_type": "annual_report", "fiscal_year": 2024},
    )

    assert len(chunks) >= 2
    assert chunks[0]["section"] == "Risk Factors"
    assert chunks[0]["metadata_json"]["ticker"] == "PDD"
    assert chunks[0]["token_count"] is not None
