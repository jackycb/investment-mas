"""Unified parsing service for downloaded filing documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.utils.html_parser import parse_html
from app.utils.pdf_parser import parse_pdf
from app.utils.text_cleaner import clean_text

logger = get_logger(__name__)


class DocumentParserService:
    """Parse downloaded filings into normalized text records."""

    def parse(self, local_path: str, file_type: str | None = None) -> list[dict[str, Any]]:
        """Parse a local filing file, tolerating parser failures."""

        suffix = (file_type or Path(local_path).suffix.lstrip(".")).lower()
        try:
            if suffix == "pdf":
                return parse_pdf(local_path)
            if suffix in {"html", "htm"}:
                return parse_html(local_path)
            if suffix == "txt":
                text = clean_text(Path(local_path).read_text(encoding="utf-8", errors="ignore"))
                return [{"page_number": None, "section": "Document", "text": text}] if text else []
        except Exception as exc:
            logger.warning("filing_parse_failed", local_path=local_path, file_type=suffix, error=str(exc))
            return []
        return []
