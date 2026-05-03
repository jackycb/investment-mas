"""Download providers for filing documents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlparse
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.document_storage_service import DocumentStorageService

logger = get_logger(__name__)
settings = get_settings()


class FilingDownloadProvider(ABC):
    """Abstract provider for downloading remote filing documents."""

    @abstractmethod
    def download_filing(self, filing_meta: dict[str, Any]) -> dict[str, Any]:
        """Download a filing and return persisted file metadata."""


class HttpFilingDownloadProvider(FilingDownloadProvider):
    """HTTP-based downloader that persists downloaded filings locally."""

    def __init__(
        self,
        storage_service: DocumentStorageService | None = None,
        timeout_seconds: float | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.storage_service = storage_service or DocumentStorageService()
        self.timeout_seconds = timeout_seconds or settings.web_request_timeout_seconds
        self.user_agent = user_agent or settings.web_user_agent

    def download_filing(self, filing_meta: dict[str, Any]) -> dict[str, Any]:
        url = str(filing_meta.get("file_url") or filing_meta.get("source_url") or "").strip()
        if not url:
            return {"status": "failed", "error": "missing file_url"}

        errors: list[str] = []
        for attempt in range(1, 4):
            headers = self._build_headers(url, filing_meta)
            try:
                logger.info("filing_download_started", file_url=url, source_name=filing_meta.get("source_name"), attempt=attempt)
                with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                    response = client.get(url, headers=headers)
                    response.raise_for_status()
                logger.info(
                    "filing_download_completed",
                    file_url=url,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                    attempt=attempt,
                )
                return self.storage_service.store_download(
                    filing_meta=filing_meta,
                    content=response.content,
                    mime_type=response.headers.get("content-type"),
                )
            except Exception as exc:
                errors.append(str(exc))
                logger.warning("filing_download_failed", file_url=url, error=str(exc), attempt=attempt)

        return {"status": "failed", "error": " | ".join(errors[-2:]) if errors else "download_failed"}

    def _build_headers(self, url: str, filing_meta: dict[str, Any]) -> dict[str, str]:
        """Build request headers, with SEC-specific hardening for official filings."""

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
        }
        host = (urlparse(url).netloc or "").lower()
        if "sec.gov" in host:
            headers.update(
                {
                    "Accept-Language": "en-US,en;q=0.9",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Referer": str(filing_meta.get("source_url") or "https://www.sec.gov/edgar/search/"),
                }
            )
        return headers
