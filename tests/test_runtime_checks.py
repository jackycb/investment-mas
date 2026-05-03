"""Tests for startup runtime dependency guards."""

from __future__ import annotations

import importlib.util

import pytest

from app.core.runtime_checks import validate_runtime_dependencies


class _Settings:
    def __init__(self, enable_filing_ingestion: bool = True, vector_store_type: str = "chroma") -> None:
        self.enable_filing_ingestion = enable_filing_ingestion
        self.vector_store_type = vector_store_type


def test_runtime_check_fails_fast_when_chromadb_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong Python runtimes should fail at startup instead of silently degrading RAG."""

    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "chromadb":
            return None
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    with pytest.raises(RuntimeError, match="ChromaDB is required"):
        validate_runtime_dependencies(_Settings())


def test_runtime_check_allows_explicit_noop_vector_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Developers may still opt out explicitly with VECTOR_STORE_TYPE=none."""

    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args, **kwargs: None)

    validate_runtime_dependencies(_Settings(vector_store_type="none"))
