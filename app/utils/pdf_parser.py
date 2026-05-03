"""PDF parsing helpers for filing ingestion."""

from __future__ import annotations

from pathlib import Path

from app.utils.text_cleaner import clean_text, remove_repetitive_noise


def parse_pdf(path: str | Path) -> list[dict]:
    """Parse PDF into page-aware text records."""

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[dict] = []
    for index, page in enumerate(reader.pages, start=1):
        text = clean_text(page.extract_text() or "")
        if not text:
            continue
        lines = remove_repetitive_noise(text.splitlines())
        if not lines:
            continue
        pages.append(
            {
                "page_number": index,
                "section": lines[0][:200] if lines else None,
                "text": "\n".join(lines),
            }
        )
    return pages
