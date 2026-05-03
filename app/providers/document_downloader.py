"""Document download abstraction for company profiling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.document_discovery_provider import DocumentCandidate

logger = get_logger(__name__)
settings = get_settings()


class DocumentDownloader(ABC):
    """Abstract document downloader."""

    @abstractmethod
    def download(self, candidate: DocumentCandidate) -> dict | None:
        """Download a document and return metadata for persistence."""


class HttpDocumentDownloader(DocumentDownloader):
    """Download document binaries or HTML pages to local storage."""

    def __init__(self, base_dir: str | None = None, timeout_seconds: float | None = None) -> None:
        self.base_dir = Path(base_dir or settings.company_document_storage_dir)
        self.timeout_seconds = timeout_seconds or settings.web_request_timeout_seconds

    def download(self, candidate: DocumentCandidate) -> dict | None:
        """Download and persist one document candidate."""

        try:
            with httpx.Client(timeout=self.timeout_seconds, headers={"User-Agent": settings.web_user_agent}, follow_redirects=True) as client:
                response = client.get(candidate.source_url)
                response.raise_for_status()
        except Exception as exc:
            logger.info("document_download_failed", ticker=candidate.ticker, source_url=candidate.source_url, error=str(exc))
            return None

        suffix = self._detect_suffix(candidate.source_url, response.headers.get("content-type", ""))
        target_dir = self.base_dir / candidate.ticker.upper()
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = candidate.fiscal_year or datetime.now(timezone.utc).year
        target_path = target_dir / f"{stamp}_{candidate.doc_type}{suffix}"
        target_path.write_bytes(response.content)
        checksum = hashlib.sha256(response.content).hexdigest()
        metadata = {
            **asdict(candidate),
            "local_path": str(target_path),
            "checksum": checksum,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "content_type": response.headers.get("content-type", ""),
        }
        logger.info("document_downloaded", ticker=candidate.ticker, doc_type=candidate.doc_type, local_path=str(target_path))
        return metadata

    def _detect_suffix(self, source_url: str, content_type: str) -> str:
        """Infer local file suffix from URL or content type."""

        path = urlparse(source_url).path.lower()
        if path.endswith(".pdf") or "pdf" in content_type.lower():
            return ".pdf"
        if path.endswith(".htm") or path.endswith(".html") or "html" in content_type.lower():
            return ".html"
        return ".bin"
