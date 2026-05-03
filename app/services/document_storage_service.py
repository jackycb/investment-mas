"""Local document storage service for downloaded filings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.utils.hashing import sha256_bytes
from app.utils.html_parser import parse_html_bytes_to_text
from app.utils.metadata_utils import json_safe
from app.utils.pdf_parser import parse_pdf

logger = get_logger(__name__)
settings = get_settings()


class DocumentStorageService:
    """Persist downloaded filing files into a stable local repository."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.document_storage_dir)

    def store_download(self, filing_meta: dict[str, Any], content: bytes, mime_type: str | None = None) -> dict[str, Any]:
        """Store downloaded bytes and return stable local metadata."""

        checksum = sha256_bytes(content)
        target_dir = self._target_dir(filing_meta)
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = self._infer_suffix(filing_meta, mime_type)
        persisted_content = content
        stored_file_type = suffix.lstrip(".") or str(filing_meta.get("file_type") or "")
        raw_local_path: Path | None = None
        text_local_path: Path | None = None
        if suffix == ".html":
            raw_local_path = target_dir / self._raw_file_name(filing_meta, ".html")
            if not raw_local_path.exists():
                raw_local_path.write_bytes(content)
            text_payload = parse_html_bytes_to_text(content)
            persisted_content = text_payload.encode("utf-8")
            suffix = ".txt"
            stored_file_type = "txt"
        elif suffix == ".pdf":
            raw_local_path = target_dir / self._stored_file_name(filing_meta, ".pdf", checksum)
            if not raw_local_path.exists() or sha256_bytes(raw_local_path.read_bytes()) != checksum:
                raw_local_path.write_bytes(content)
                logger.info("filing_pdf_saved_to_local_storage", local_path=str(raw_local_path), checksum=checksum)
            text_payload = self._pdf_bytes_to_text(raw_local_path)
            if text_payload.strip():
                persisted_content = text_payload.encode("utf-8")
                text_local_path = raw_local_path.with_suffix(".txt")
                if not text_local_path.exists() or text_local_path.read_bytes() != persisted_content:
                    text_local_path.write_bytes(persisted_content)
                    logger.info("filing_text_saved_to_local_storage", local_path=str(text_local_path), checksum=checksum)
                suffix = ".txt"
                stored_file_type = "txt"
        if text_local_path is not None:
            local_path = text_local_path
        else:
            file_name = self._stored_file_name(filing_meta, suffix, checksum)
            local_path = target_dir / file_name
            if not local_path.exists() or sha256_bytes(local_path.read_bytes()) != sha256_bytes(persisted_content):
                local_path.write_bytes(persisted_content)
                logger.info("filing_saved_to_local_storage", local_path=str(local_path), checksum=checksum)
            if suffix == ".txt":
                text_local_path = local_path
        sidecar = local_path.with_suffix(local_path.suffix + ".meta.json")
        sidecar.write_text(
            json.dumps(
                json_safe(
                    {
                        **filing_meta,
                        "checksum": checksum,
                        "mime_type": mime_type,
                        "stored_file_type": stored_file_type,
                        "stored_suffix": suffix,
                        "raw_file_type": filing_meta.get("file_type"),
                        "raw_local_path": str(raw_local_path) if raw_local_path else None,
                        "text_local_path": str(text_local_path) if text_local_path else None,
                    }
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            **filing_meta,
            "status": "downloaded",
            "checksum": checksum,
            "file_size": len(persisted_content),
            "mime_type": mime_type,
            "local_path": str(local_path),
            "file_type": stored_file_type,
            "stored_file_type": stored_file_type,
            "stored_suffix": suffix,
            "raw_local_path": str(raw_local_path) if raw_local_path else None,
            "raw_file_type": filing_meta.get("file_type"),
            "text_local_path": str(text_local_path) if text_local_path else None,
        }

    def _target_dir(self, filing_meta: dict[str, Any]) -> Path:
        market = str(filing_meta.get("market") or "UNKNOWN").upper()
        ticker = str(filing_meta.get("ticker") or "UNKNOWN").upper()
        filing_type = str(filing_meta.get("filing_type") or "filing")
        fiscal_year = str(filing_meta.get("fiscal_year") or "unknown")
        return self.base_dir / market / ticker / filing_type / fiscal_year

    def _infer_suffix(self, filing_meta: dict[str, Any], mime_type: str | None) -> str:
        file_url = str(filing_meta.get("file_url") or "")
        lower_url = file_url.lower()
        if lower_url.endswith(".pdf") or (mime_type and "pdf" in mime_type.lower()):
            return ".pdf"
        if lower_url.endswith(".txt") or (mime_type and "text/plain" in mime_type.lower()):
            return ".txt"
        return ".html"

    def _raw_file_name(self, filing_meta: dict[str, Any], suffix: str) -> str:
        """Build a stable companion raw-file name for source preservation."""

        return (
            f"{filing_meta.get('ticker', 'UNKNOWN')}_"
            f"{filing_meta.get('filing_type', 'filing')}_"
            f"{filing_meta.get('fiscal_year', 'na')}_"
            f"{self._source_slug(filing_meta)}.raw{suffix}"
        )

    def _stored_file_name(self, filing_meta: dict[str, Any], suffix: str, checksum: str) -> str:
        """Build a stable but collision-resistant stored file name."""

        return (
            f"{filing_meta.get('ticker', 'UNKNOWN')}_"
            f"{filing_meta.get('filing_type', 'filing')}_"
            f"{filing_meta.get('fiscal_year', 'na')}_"
            f"{self._source_slug(filing_meta)}_"
            f"{checksum[:10]}{suffix}"
        )

    def _source_slug(self, filing_meta: dict[str, Any]) -> str:
        """Derive a readable source slug from the filing URL."""

        url = str(filing_meta.get("file_url") or filing_meta.get("source_url") or "")
        path = urlparse(url).path.strip("/")
        candidate = Path(path).stem or filing_meta.get("source_name") or "source"
        slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(candidate))
        slug = "_".join(part for part in slug.split("_") if part)[:48]
        return slug or "source"

    def _pdf_bytes_to_text(self, raw_local_path: Path) -> str:
        """Best-effort PDF text extraction for downstream chunking and retrieval."""

        try:
            pages = parse_pdf(raw_local_path)
        except Exception as exc:
            logger.warning("pdf_text_conversion_failed", raw_local_path=str(raw_local_path), error=str(exc))
            return ""
        return "\n\n".join(page.get("text", "") for page in pages if page.get("text"))
