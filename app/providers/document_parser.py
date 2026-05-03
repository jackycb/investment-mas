"""Document parsing abstraction for company profiling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import re
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class DocumentParser(ABC):
    """Abstract parser for downloaded company documents."""

    @abstractmethod
    def parse(self, document_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse a downloaded document into normalized chunks."""


class HybridDocumentParser(DocumentParser):
    """Parse PDF and HTML documents into standardized chunks."""

    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None) -> None:
        self.chunk_size = chunk_size or settings.company_profile_chunk_size
        self.chunk_overlap = chunk_overlap or settings.company_profile_chunk_overlap

    def parse(self, document_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse a document based on file extension."""

        path = Path(str(document_metadata.get("local_path", "")))
        if not path.exists():
            return []
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._parse_pdf(path, document_metadata)
        if suffix in {".html", ".htm"}:
            return self._parse_html(path, document_metadata)
        logger.info("document_parser_unsupported_suffix", local_path=str(path), suffix=suffix)
        return []

    def _parse_pdf(self, path: Path, document_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse PDF pages and split them into chunks."""

        try:
            from pypdf import PdfReader
        except Exception as exc:
            logger.warning("document_parser_pdf_unavailable", error=str(exc))
            return []

        reader = PdfReader(str(path))
        chunks: list[dict[str, Any]] = []
        for page_index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            section = self._infer_section(text)
            for chunk_index, chunk_text in enumerate(self._split_text(text), start=1):
                chunks.append(self._build_chunk(document_metadata, chunk_text, section, page_index, chunk_index))
        return chunks

    def _parse_html(self, path: Path, document_metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse HTML sections and split them into chunks."""

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "lxml")
            chunks: list[dict[str, Any]] = []
            current_section = "general"
            bucket: list[str] = []
            section_index = 0
            for node in soup.find_all(["h1", "h2", "h3", "p", "li"]):
                text = " ".join(node.get_text(" ", strip=True).split())
                if not text:
                    continue
                if node.name in {"h1", "h2", "h3"}:
                    if bucket:
                        section_index += 1
                        chunks.extend(self._emit_section_chunks(document_metadata, current_section, bucket, section_index))
                        bucket = []
                    current_section = text[:255]
                    continue
                bucket.append(text)
            if bucket:
                section_index += 1
                chunks.extend(self._emit_section_chunks(document_metadata, current_section, bucket, section_index))
            return chunks
        except Exception as exc:
            logger.warning("document_parser_html_bs4_unavailable", error=str(exc))

        try:
            from lxml import html
        except Exception as exc:
            logger.warning("document_parser_html_unavailable", error=str(exc))
            return []

        try:
            tree = html.fromstring(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception as exc:
            logger.warning("document_parser_html_parse_failed", error=str(exc))
            return []

        chunks: list[dict[str, Any]] = []
        current_section = "general"
        bucket: list[str] = []
        section_index = 0
        for node in tree.xpath("//h1|//h2|//h3|//p|//li"):
            text = " ".join("".join(node.itertext()).split())
            if not text:
                continue
            if node.tag in {"h1", "h2", "h3"}:
                if bucket:
                    section_index += 1
                    chunks.extend(self._emit_section_chunks(document_metadata, current_section, bucket, section_index))
                    bucket = []
                current_section = text[:255]
                continue
            bucket.append(text)
        if bucket:
            section_index += 1
            chunks.extend(self._emit_section_chunks(document_metadata, current_section, bucket, section_index))
        return chunks

    def _emit_section_chunks(
        self,
        document_metadata: dict[str, Any],
        section: str,
        bucket: list[str],
        section_index: int,
    ) -> list[dict[str, Any]]:
        text = "\n".join(bucket)
        return [
            self._build_chunk(document_metadata, chunk_text, section, None, section_index * 100 + idx)
            for idx, chunk_text in enumerate(self._split_text(text), start=1)
        ]

    def _split_text(self, text: str) -> list[str]:
        """Split long text into overlapping chunks."""

        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return []
        if len(normalized) <= self.chunk_size:
            return [normalized]
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + self.chunk_size)
            chunks.append(normalized[start:end].strip())
            if end >= len(normalized):
                break
            start = max(end - self.chunk_overlap, start + 1)
        return chunks

    def _infer_section(self, text: str) -> str:
        """Infer a section heading from PDF text."""

        for line in text.splitlines()[:8]:
            stripped = line.strip()
            if 4 <= len(stripped) <= 120:
                return stripped[:255]
        return "general"

    def _build_chunk(
        self,
        document_metadata: dict[str, Any],
        text: str,
        section: str,
        page_number: int | None,
        chunk_index: int,
    ) -> dict[str, Any]:
        """Build standardized chunk payload."""

        return {
            "text": text,
            "section": section or "general",
            "page_number": page_number,
            "doc_type": document_metadata.get("doc_type"),
            "fiscal_year": document_metadata.get("fiscal_year"),
            "ticker": document_metadata.get("ticker"),
            "company_name": document_metadata.get("company_name"),
            "chunk_index": chunk_index,
        }
