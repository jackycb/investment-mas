"""Runtime dependency guards for production startup."""

from __future__ import annotations

import importlib.util
import sys
from typing import Protocol


class _RuntimeSettings(Protocol):
    enable_filing_ingestion: bool
    vector_store_type: str


def validate_runtime_dependencies(settings: _RuntimeSettings) -> None:
    """Fail fast when required runtime dependencies are unavailable.

    Filing RAG should never silently degrade because the API was launched with
    the wrong Python interpreter. If Chroma is configured, the process must be
    able to import ``chromadb`` before serving requests.
    """

    if not settings.enable_filing_ingestion:
        return
    if settings.vector_store_type.lower() != "chroma":
        return
    if importlib.util.find_spec("chromadb") is not None:
        return
    raise RuntimeError(
        "ChromaDB is required for filing RAG but is not installed in the active "
        f"Python runtime: {sys.executable}. Start the API with ./scripts/run_api.sh "
        "or install dependencies into this runtime with: python -m pip install -e '.[dev]'"
    )
