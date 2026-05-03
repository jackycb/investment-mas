"""Stock price provider — reads current price from local info.csv or web search."""

from __future__ import annotations

import csv
import re
from io import StringIO
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def get_current_price(ticker: str) -> dict[str, Any]:
    """Return current stock price and basic info for a ticker.

    Tries local info.csv first, then falls back to web search.
    Returns dict with keys: price, currency, market_cap, pe_ratio, pb_ratio,
    dividend_yield, shares_outstanding, source.
    """

    result = _read_local_info(ticker)
    if result and result.get("price"):
        return result

    result = _web_search_price(ticker)
    if result and result.get("price"):
        return result

    return {"price": None, "source": "unavailable"}


def _read_local_info(ticker: str) -> dict[str, Any] | None:
    """Read price from data/finance_report_web/{ticker}_info.csv."""

    base = Path(settings.local_financial_base_dir)
    info_path = base / f"{ticker}_info.csv"
    if not info_path.exists():
        return None

    try:
        text = info_path.read_text(encoding="utf-8-sig")
        data: dict[str, str] = {}
        for line in csv.reader(StringIO(text)):
            if len(line) >= 2:
                data[line[0].strip()] = line[1].strip()

        price_str = data.get("平均价", "")
        price = _parse_number(price_str)
        if not price:
            return None

        market_cap_str = data.get("总市值", "")
        shares_str = data.get("总股本", "")

        return {
            "price": price,
            "market_cap_raw": market_cap_str,
            "shares_outstanding_raw": shares_str,
            "pe_ratio": _parse_number(data.get("市盈率", "")),
            "pb_ratio": _parse_number(data.get("市净率", "")),
            "dividend_yield": _parse_pct(data.get("股息率", "")),
            "source": "local_info_csv",
        }
    except Exception as exc:
        logger.warning("local_info_read_failed", ticker=ticker, error=str(exc))
        return None


def _web_search_price(ticker: str) -> dict[str, Any] | None:
    """Attempt to fetch price via a simple web request."""

    bare = ticker.split(".")[-1] if "." in ticker else ticker
    try:
        import httpx
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{bare}?range=1d&interval=1d"
        r = httpx.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        body = r.json()
        meta = body.get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        if price:
            return {
                "price": float(price),
                "currency": meta.get("currency", ""),
                "source": "yahoo_finance",
            }
    except Exception as exc:
        logger.warning("web_price_fetch_failed", ticker=ticker, error=str(exc))
    return None


def _parse_number(s: str) -> float | None:
    """Parse a numeric string, returning None for '--' or empty."""

    s = s.strip().replace(",", "")
    if not s or s == "--":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_pct(s: str) -> float | None:
    """Parse a percentage string like '0.02%', returning as decimal."""

    s = s.strip()
    if not s or s == "--":
        return None
    m = re.match(r"([\d.]+)%", s)
    if m:
        return float(m.group(1)) / 100
    return _parse_number(s)
