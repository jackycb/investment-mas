"""Financial data provider interfaces and implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import threading

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.company_repository import get_company_by_ticker
from app.db.repositories.financial_repository import get_annual_reports
from app.services.compute_cache_service import CACHE_MARKET, cache_key, get_cache
from app.services.futu_financial_download_service import FutuFinancialDownloadService
from app.services.performance_tracker import SPAN_KIND_IO, record_cache_event, span
from app.utils.file_discovery import discover_financial_files
from app.utils.financial_mapping import finalize_financial_row, parse_numeric_value
from app.utils.financial_parser import parse_financial_file

logger = get_logger(__name__)

# Serializes concurrent market fetches so parallel specialists warm one cache
# instead of each paying the 5–11 s network cost for the same tickers.
_YAHOO_FETCH_LOCK = threading.Lock()


class FinancialDataProvider(ABC):
    """Abstract provider for financial history."""

    @abstractmethod
    def load_company_financials(self, ticker: str, company_name: str, years: int = 5) -> list[dict]:
        """Return normalized annual financial reports for a company."""

    @abstractmethod
    def load_peer_financials(self, peer_tickers: list[str], years: int = 5) -> dict[str, list[dict]]:
        """Return normalized annual financial reports for peer tickers."""


class LocalFinancialDataProvider(FinancialDataProvider):
    """Local directory-backed provider with tolerant parsing and mapping."""

    def __init__(self, base_dir: str | None = None) -> None:
        current_settings = get_settings()
        self.base_dir = base_dir or current_settings.local_financial_base_dir
        self._index: dict[str, dict[str, Path]] | None = None
        self.downloader = FutuFinancialDownloadService(base_dir=self.base_dir)
        self._recent_futu_downloads: set[str] = set()

    def load_company_financials(self, ticker: str, company_name: str, years: int = 5) -> list[dict]:
        """Load company financials from the configured local directory."""

        ticker_candidates = self._ticker_candidates(ticker)
        ticker_key = ticker_candidates[0]
        files: dict[str, Path] = {}
        resolved_ticker = ticker_key
        index = self._get_index()
        for candidate in ticker_candidates:
            files = index.get(candidate, {})
            if files:
                resolved_ticker = candidate
                break
        if not files:
            logger.warning("local_financial_data_missing", ticker=ticker_key, company_name=company_name, base_dir=self.base_dir)
            files, resolved_ticker = self._attempt_download_refresh(ticker_key, ticker_candidates, index)
            if not files:
                return []

        merged = self._merge_company_files(resolved_ticker, files)
        current_settings = get_settings()
        if (
            not merged
            and current_settings.enable_futu_download_fallback
            and Path(self.base_dir).resolve() == Path(current_settings.local_financial_base_dir).resolve()
        ):
            logger.info("local_financial_incomplete_triplet", ticker=ticker_key, available_files=sorted(files.keys()))
            files, resolved_ticker = self._attempt_download_refresh(ticker_key, ticker_candidates, self._get_index())
            merged = self._merge_company_files(resolved_ticker, files) if files else []
        if not merged:
            logger.warning("local_financial_no_records", ticker=ticker_key, company_name=company_name)
        return merged[:years]

    def load_peer_financials(self, peer_tickers: list[str], years: int = 5) -> dict[str, list[dict]]:
        """Load peer financials for each peer ticker."""

        results: dict[str, list[dict]] = {}
        index = self._get_index()
        for ticker in peer_tickers:
            ticker_candidates = self._ticker_candidates(ticker)
            files: dict[str, Path] = {}
            resolved_ticker = ticker.upper().strip()
            for candidate in ticker_candidates:
                files = index.get(candidate, {})
                if files:
                    resolved_ticker = candidate
                    break
            if not files:
                logger.info("local_peer_financial_missing", ticker=resolved_ticker, base_dir=self.base_dir)
                results[ticker] = []
                continue

            merged = self._merge_company_files(resolved_ticker, files)
            if not merged:
                logger.info(
                    "local_peer_financial_incomplete",
                    ticker=resolved_ticker,
                    available_files=sorted(files.keys()),
                    base_dir=self.base_dir,
                )
                results[ticker] = []
                continue
            results[ticker] = merged[:years]
        return results

    def available_tickers(self) -> list[str]:
        """Return all tickers discovered under the local financial directory."""

        return sorted(self._get_index().keys())

    def files_for_ticker(self, ticker: str) -> dict[str, Path]:
        """Return discovered source files for a ticker."""

        return dict(self._get_index().get(ticker.upper(), {}))

    def _get_index(self) -> dict[str, dict[str, Path]]:
        if self._index is not None:
            return self._index

        index: dict[str, dict[str, Path]] = {}
        for path in discover_financial_files(self.base_dir):
            stem = path.stem
            if "_" in stem:
                ticker, file_kind = stem.split("_", 1)
            else:
                ticker, file_kind = stem, "misc"
            index.setdefault(ticker.upper(), {})[file_kind] = path
        self._index = index
        logger.info("local_financial_index_built", base_dir=self.base_dir, ticker_count=len(index))
        return index

    def _merge_company_files(self, ticker: str, files: dict[str, Path]) -> list[dict]:
        merged: dict[int, dict] = {}
        info_payload: dict[str, object] = {}
        warnings: list[str] = []

        for file_kind, path in files.items():
            parsed = parse_financial_file(path)
            warnings.extend(parsed.get("warnings", []))
            if file_kind == "info" and parsed["records"]:
                info_payload = parsed["records"][0]
                continue
            for record in parsed["records"]:
                year = record.get("fiscal_year")
                if year is None:
                    continue
                row = merged.setdefault(
                    year,
                    {
                        "ticker": ticker,
                        "company_name": info_payload.get("股票名称") or info_payload.get("股票名稱") or info_payload.get("company_name"),
                        "period_type": "annual",
                        "fiscal_period": "FY",
                        "raw_source": {"file_names": [], "source_kind": "local_directory", "base_dir": self.base_dir},
                    },
                )
                row.update({key: value for key, value in record.items() if value is not None})
                row["raw_source"].setdefault("file_names", []).append(path.name)

        if info_payload:
            shares = parse_numeric_value(info_payload.get("总股本") or info_payload.get("總股本"))
            for row in merged.values():
                row.setdefault("company_name", info_payload.get("股票名称") or info_payload.get("股票名稱"))
                if shares is not None:
                    row.setdefault("shares_outstanding", shares)
        if ticker in self._recent_futu_downloads:
            for row in merged.values():
                raw_source = dict(row.get("raw_source") or {})
                raw_source["downloaded_via"] = "futu_downloader"
                raw_source["download_script"] = str(self.downloader.script_path)
                row["raw_source"] = raw_source

        records = [finalize_financial_row(row) for _, row in sorted(merged.items(), reverse=True)]
        if warnings:
            logger.warning("local_financial_parse_warnings", ticker=ticker, warnings=warnings[:10])
        return records

    # Common dual-listed / alias ticker mappings (bidirectional)
    _TICKER_ALIASES: dict[str, list[str]] = {
        "HK.09988": ["US.BABA", "BABA"],
        "US.BABA": ["HK.09988", "BABA"],
        "BABA": ["HK.09988", "US.BABA"],
        "HK.09618": ["US.JD", "JD"],
        "US.JD": ["HK.09618", "JD"],
        "JD": ["HK.09618", "US.JD"],
        "HK.09888": ["US.BIDU", "BIDU"],
        "US.BIDU": ["HK.09888", "BIDU"],
        "HK.09999": ["US.NIO", "NIO"],
        "US.NIO": ["HK.09999", "NIO"],
        "HK.01024": ["US.KS", "KS"],
        "HK.09961": ["US.GDS", "GDS"],
        "HK.09633": ["US.NTES", "NTES"],
        "US.NTES": ["HK.09633", "NTES"],
        "HK.09896": ["US.BILI", "BILI"],
        "US.BILI": ["HK.09896", "BILI"],
        "HK.01138": ["HK.09988", "US.BABA", "BABA"],
    }

    def _ticker_candidates(self, ticker: str) -> list[str]:
        """Return reasonable local-file ticker candidates for a user input ticker."""

        normalized = ticker.upper().strip()
        candidates = [normalized]
        if "." not in normalized:
            if normalized.isalpha() and len(normalized) <= 5:
                candidates.append(f"US.{normalized}")
            if normalized.isdigit() and len(normalized) <= 5:
                candidates.append(f"HK.{normalized.zfill(5)}")

        for alias in self._TICKER_ALIASES.get(normalized, []):
            if alias not in candidates:
                candidates.append(alias)

        return candidates

    def _attempt_download_refresh(
        self,
        ticker_key: str,
        ticker_candidates: list[str],
        index: dict[str, dict[str, Path]],
    ) -> tuple[dict[str, Path], str]:
        """Try Futu download and refresh local index."""

        current_settings = get_settings()
        if not (
            current_settings.enable_futu_download_fallback
            and Path(self.base_dir).resolve() == Path(current_settings.local_financial_base_dir).resolve()
        ):
            return {}, ticker_key
        download_result = self.downloader.download(ticker_key)
        logger.info("futu_download_attempted", ticker=ticker_key, result=download_result)
        if bool(download_result.get("success")):
            self._index = None
            index = self._get_index()
            for candidate in ticker_candidates:
                files = index.get(candidate, {})
                if files:
                    self._recent_futu_downloads.add(candidate)
                    return files, candidate
        return {}, ticker_key


class DatabaseFinancialDataProvider(FinancialDataProvider):
    """Database-backed financial provider."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def load_company_financials(self, ticker: str, company_name: str, years: int = 5) -> list[dict]:
        company = get_company_by_ticker(self.session, ticker)
        if not company:
            return []
        reports = get_annual_reports(self.session, company_id=company.id, limit=years)
        return [
            {
                "ticker": ticker.upper(),
                "company_name": company.company_name,
                "fiscal_year": report.fiscal_year,
                "report_date": report.report_date.isoformat(),
                "revenue": float(report.revenue) if report.revenue is not None else None,
                "gross_profit": float(report.gross_profit) if report.gross_profit is not None else None,
                "operating_profit": float(report.operating_profit) if report.operating_profit is not None else None,
                "net_income": float(report.net_income) if report.net_income is not None else None,
                "operating_cash_flow": float(report.operating_cash_flow) if report.operating_cash_flow is not None else None,
                "free_cash_flow": float(report.free_cash_flow) if report.free_cash_flow is not None else None,
                "total_assets": float(report.total_assets) if report.total_assets is not None else None,
                "total_liabilities": float(report.total_liabilities) if report.total_liabilities is not None else None,
                "total_equity": float(report.total_equity) if report.total_equity is not None else None,
                "shares_outstanding": float(report.shares_outstanding) if report.shares_outstanding is not None else None,
                "gross_margin": report.gross_margin,
                "net_margin": report.net_margin,
                "roe": report.roe,
                "roa": report.roa,
                "debt_ratio": report.debt_ratio,
                "period_type": report.period_type,
                "fiscal_period": report.fiscal_period,
                "raw_source": (report.raw_source or {}) | {"source_kind": "database"},
            }
            for report in reports
        ]

    def load_peer_financials(self, peer_tickers: list[str], years: int = 5) -> dict[str, list[dict]]:
        return {ticker: self.load_company_financials(ticker, ticker, years=years) for ticker in peer_tickers}


def _to_yahoo_symbol(ticker: str) -> str | None:
    """Convert project ticker (HK.09988, US.AMZN) to Yahoo Finance symbol."""
    t = ticker.strip().upper()
    if "." not in t:
        return t if t.isalpha() else None
    market, sym = t.split(".", 1)
    if market == "HK":
        return f"{int(sym):04d}.HK" if sym.isdigit() else f"{sym}.HK"
    if market == "SH":
        return f"{sym}.SS"
    if market == "SZ":
        return f"{sym}.SZ"
    if market == "US":
        return sym
    if market == "JP":
        return f"{sym}.T"
    return None


def fetch_yahoo_peer_financials(peer_tickers: list[str], *, force_refresh: bool = False) -> dict[str, list[dict]]:
    """Fetch key financial metrics from Yahoo Finance for peer tickers.

    Results are memoized per ticker in a short-TTL process cache. Measured
    baseline for this call was 5.6–11.2 s for five peers, and a single research
    run used to invoke it twice for nearly the same ticker set (once from
    ``FinancialService.compare_peers``, once from ``PeerAnalystNode``), wasting
    11–17 s of pure duplicate network I/O. Caching here fixes every call site at
    once without changing the returned payload shape.

    Empty results are cached too — a ticker Yahoo has no revenue data for will
    not keep being retried inside the same run.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance_not_installed")
        return {}

    ticker_map: dict[str, str] = {}
    for t in peer_tickers:
        yahoo_sym = _to_yahoo_symbol(t)
        if yahoo_sym:
            ticker_map[t] = yahoo_sym

    if not ticker_map:
        return {}

    # One shared lock: parallel specialists warm a single cache instead of each
    # paying the full network cost for overlapping ticker sets.
    with _YAHOO_FETCH_LOCK:
        return _fetch_yahoo_peer_financials_locked(
            ticker_map, yf, requested=len(peer_tickers), force_refresh=force_refresh
        )


def _fetch_yahoo_peer_financials_locked(
    ticker_map: dict[str, str], yf, *, requested: int, force_refresh: bool = False
) -> dict[str, list[dict]]:
    """Cache-aware Yahoo TTM fetch. Caller must hold ``_YAHOO_FETCH_LOCK``."""

    cache = get_cache(CACHE_MARKET)
    results: dict[str, list[dict]] = {}
    misses: dict[str, str] = {}
    for orig_ticker, yahoo_sym in ticker_map.items():
        key = cache_key("yahoo_ttm", orig_ticker, yahoo_sym)
        if not force_refresh:
            cached = cache.get(key)
            if cached is not None:
                results[orig_ticker] = cached
                record_cache_event(True)
                continue
        record_cache_event(False)
        misses[orig_ticker] = yahoo_sym

    if not misses:
        logger.info("yahoo_peer_financials_cache_hit", requested=requested, cached=len(results))
        return results

    yahoo_symbols = " ".join(misses.values())
    try:
        batch = yf.Tickers(yahoo_symbols)
    except Exception as exc:
        logger.warning("yahoo_batch_init_failed", error=str(exc))
        # Keep whatever was cached; do not poison the cache with failures.
        for orig_ticker in misses:
            results.setdefault(orig_ticker, [])
        return results

    with span("yahoo_peer_fetch", span_kind=SPAN_KIND_IO, skill_name="market_data"):
        for orig_ticker, yahoo_sym in misses.items():
            try:
                info = batch.tickers[yahoo_sym].info
                if not info or not info.get("totalRevenue"):
                    results[orig_ticker] = []
                    cache.set(cache_key("yahoo_ttm", orig_ticker, yahoo_sym), [])
                    continue
                record = {
                    "ticker": orig_ticker,
                    "company_name": info.get("shortName") or info.get("longName") or orig_ticker,
                    "currency": info.get("financialCurrency") or info.get("currency") or "USD",
                    "industry": info.get("industry"),
                    "fiscal_year": None,
                    "revenue": info.get("totalRevenue"),
                    "revenue_growth": info.get("revenueGrowth"),
                    "gross_margin": info.get("grossMargins"),
                    "operating_margin": info.get("operatingMargins"),
                    "net_margin": info.get("profitMargins"),
                    "roe": info.get("returnOnEquity"),
                    "roa": info.get("returnOnAssets"),
                    "roic": None,
                    "free_cash_flow": info.get("freeCashflow"),
                    "operating_cash_flow": info.get("operatingCashflow"),
                    "total_debt": info.get("totalDebt"),
                    "total_cash": info.get("totalCash"),
                    "debt_to_equity": info.get("debtToEquity"),
                    "current_ratio": info.get("currentRatio"),
                    "market_cap": info.get("marketCap"),
                    "trailing_pe": info.get("trailingPE"),
                    "forward_pe": info.get("forwardPE"),
                    "price_to_book": info.get("priceToBook"),
                    "earnings_growth": info.get("earningsGrowth"),
                    "period_type": "ttm",
                    "raw_source": {"source_kind": "yahoo_finance", "yahoo_symbol": yahoo_sym},
                }
                results[orig_ticker] = [record]
                cache.set(cache_key("yahoo_ttm", orig_ticker, yahoo_sym), [record])
            except Exception as exc:
                logger.warning("yahoo_peer_fetch_failed", ticker=orig_ticker, yahoo_sym=yahoo_sym, error=str(exc))
                results[orig_ticker] = []

    logger.info(
        "yahoo_peer_financials_fetched",
        requested=requested,
        fetched=sum(1 for v in results.values() if v),
        cache_reused=requested - len(misses),
    )
    return results


class HybridFinancialDataProvider(FinancialDataProvider):
    """Local-first provider with database and Yahoo Finance fallbacks."""

    def __init__(self, local_provider: LocalFinancialDataProvider, database_provider: DatabaseFinancialDataProvider) -> None:
        self.local_provider = local_provider
        self.database_provider = database_provider

    def load_company_financials(self, ticker: str, company_name: str, years: int = 5) -> list[dict]:
        local_records = self.local_provider.load_company_financials(ticker, company_name, years=years)
        if local_records:
            return local_records
        return self.database_provider.load_company_financials(ticker, company_name, years=years)

    def load_peer_financials(self, peer_tickers: list[str], years: int = 5) -> dict[str, list[dict]]:
        results = self.local_provider.load_peer_financials(peer_tickers, years=years)
        missing = [ticker for ticker, rows in results.items() if not rows]
        if missing:
            db_results = self.database_provider.load_peer_financials(missing, years=years)
            for ticker in missing:
                results[ticker] = db_results.get(ticker, [])

        still_missing = [ticker for ticker, rows in results.items() if not rows]
        if still_missing:
            yahoo_results = fetch_yahoo_peer_financials(still_missing)
            for ticker in still_missing:
                results[ticker] = yahoo_results.get(ticker, [])

        return results


def build_financial_provider(session: Session) -> FinancialDataProvider:
    """Build a provider from current settings."""

    current_settings = get_settings()
    provider_mode = current_settings.financial_data_provider.lower()
    local_provider = LocalFinancialDataProvider(base_dir=current_settings.local_financial_base_dir)
    database_provider = DatabaseFinancialDataProvider(session=session)
    if provider_mode == "local":
        return local_provider
    if provider_mode == "database":
        return database_provider
    return HybridFinancialDataProvider(local_provider=local_provider, database_provider=database_provider)
