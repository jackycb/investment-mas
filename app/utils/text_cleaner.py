"""Text cleaning helpers for parsed filing content."""

from __future__ import annotations

import re

# US EDGAR filings often prepend a single massive XBRL metadata line (~15K-40K chars)
# before the real document content. Examples:
#   "0001045810 2025 FY false ... us-gaap:... iso4217:USD xbrli:shares ..."
_XBRL_INDICATORS = (
    "us-gaap:",
    "iso4217:",
    "xbrli:",
    "dei:",
    "country:",
    "srt:",
    "stpr:",
    "nvda:",
    "currency:",
)
_MIN_XBRL_LINE_LENGTH = 5000  # real XBRL lines are 15K+; stay conservative


def strip_xbrl_header(text: str) -> str:
    """Remove a leading SEC EDGAR XBRL metadata line if present.

    The first line is stripped when:
      - it is the only text on line 1 (ends with \n or is the entire string)
      - it is unusually long (>= _MIN_XBRL_LINE_LENGTH)
      - it contains one or more known XBRL namespace patterns
    """
    if not text:
        return text

    nl_idx = text.find("\n")
    first_line = text[:nl_idx] if nl_idx != -1 else text

    # Bail early: line is not suspiciously long
    if len(first_line) < _MIN_XBRL_LINE_LENGTH:
        return text

    # Check for XBRL indicator tokens (case-insensitive for safety)
    first_lower = first_line.lower()
    if any(indicator in first_lower for indicator in _XBRL_INDICATORS):
        remainder = text[nl_idx + 1:] if nl_idx != -1 else ""
        return remainder.lstrip("\n")

    return text


def clean_text(text: str) -> str:
    """Apply lightweight normalization suitable for RAG ingestion."""

    # Strip leading XBRL garbage before any other cleaning
    value = strip_xbrl_header(text)

    value = value.replace("\x00", " ").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[^\S\n]{2,}", " ", value)
    return value.strip()


def remove_repetitive_noise(lines: list[str]) -> list[str]:
    """Drop obviously empty or overly noisy lines."""

    cleaned: list[str] = []
    for line in lines:
        text = clean_text(line)
        if not text:
            continue
        if len(text) <= 2 and not text.isdigit():
            continue
        cleaned.append(text)
    return cleaned