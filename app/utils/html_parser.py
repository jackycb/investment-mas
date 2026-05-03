"""HTML parsing helpers for filing ingestion."""

from __future__ import annotations

from pathlib import Path

from lxml import html

from app.utils.text_cleaner import clean_text, remove_repetitive_noise


def parse_html(path: str | Path) -> list[dict]:
    """Parse HTML into section-aware text blocks."""

    tree = html.fromstring(Path(path).read_text(encoding="utf-8", errors="ignore"))
    return _parse_tree(tree)


def parse_html_bytes_to_text(content: bytes) -> str:
    """Convert HTML bytes into normalized plain text for local storage."""

    tree = html.fromstring(content)
    sections = _parse_tree(tree)
    return "\n\n".join(section["text"] for section in sections if section.get("text")).strip()


def _parse_tree(tree: html.HtmlElement) -> list[dict]:
    """Parse an lxml HTML tree into section-aware text blocks."""

    sections: list[dict] = []
    current_section = "Document"
    buffer: list[str] = []

    for element in tree.xpath("//*"):
        if element.tag in {"script", "style", "noscript"}:
            continue
        text = clean_text(" ".join(part.strip() for part in element.itertext() if part and part.strip()))
        if not text:
            continue
        if element.tag in {"h1", "h2", "h3"}:
            if buffer:
                lines = remove_repetitive_noise(buffer)
                if lines:
                    sections.append({"page_number": None, "section": current_section, "text": "\n".join(lines)})
                buffer = []
            current_section = text[:200]
            continue
        if element.tag in {"p", "li", "div"}:
            buffer.append(text)

    if buffer:
        lines = remove_repetitive_noise(buffer)
        if lines:
            sections.append({"page_number": None, "section": current_section, "text": "\n".join(lines)})
    return sections
