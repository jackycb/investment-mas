"""Financial data provider interfaces and implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.company_repository import get_company_by_ticker
from app.db.repositories.financial_repository import get_annual_reports
from app.services.futu_financial_download_service import FutuFinancialDownloadService
from app.utils.file_discovery import discover_financial_files
from app.utils.financial_mapping import finalize_financial_row, parse_numeric_value
from app.utils.financial_parser import parse_financial_file

logger = get_logger(__name__)


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

    def _ticker_candidates(self, ticker: str) -> list[str]:
        """Return reasonable local-file ticker candidates for a user input ticker."""

        normalized = ticker.upper().strip()
        candidates = [normalized]
        if "." not in normalized:
            if normalized.isalpha() and len(normalized) <= 5:
                candidates.append(f"US.{normalized}")
            if normalized.isdigit() and len(normalized) <= 5:
                candidates.append(f"HK.{normalized.zfill(5)}")
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


class HybridFinancialDataProvider(FinancialDataProvider):
    """Local-first provider with database fallback."""

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
