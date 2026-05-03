"""Metadata normalization helpers for filing ingestion."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """Convert nested metadata into JSON-serializable primitives."""

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def infer_market_from_ticker(ticker: str) -> str:
    """Infer a normalized market code from project ticker format."""

    text = ticker.strip().upper()
    if "." not in text:
        return "US"
    prefix, _ = text.split(".", 1)
    return {"SH": "CN", "SZ": "CN", "HK": "HK", "US": "US"}.get(prefix, prefix)


def normalize_ticker(ticker: str) -> str:
    """Ensure ticker always carries a market prefix (e.g. ``NVDA`` → ``US.NVDA``).

    Tickers that already have a recognized prefix (US/HK/SH/SZ) are
    returned as-is.  Pure-numeric tickers with 5–6 digits are treated
    as HK stocks.
    """

    text = ticker.strip().upper()
    if "." in text:
        prefix = text.split(".", 1)[0]
        if prefix in {"US", "HK", "SH", "SZ"}:
            return text
    if text.isdigit() and 5 <= len(text) <= 6:
        return f"HK.{text}"
    if text.isdigit() and len(text) == 6:
        return f"SH.{text}"
    return f"US.{text}"
