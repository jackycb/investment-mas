"""Supplemental valuation bridge based on bundled legacy scripts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.futu_financial_download_service import FutuFinancialDownloadService

logger = get_logger(__name__)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SupplementalValuationService:
    """Use the bundled `run_value_analysis_by_code.py` logic as an extra valuation source."""

    def __init__(
        self,
        script_path: str | None = None,
        helper_path: str | None = None,
        workbook_path: str | None = None,
        fx_workbook_path: str | None = None,
    ) -> None:
        settings = get_settings()
        self.script_path = Path(script_path or settings.supplemental_valuation_script_path)
        self.helper_path = Path(helper_path or settings.supplemental_valuation_helper_path)
        self.workbook_path = Path(workbook_path or settings.supplemental_valuation_workbook)
        self.fx_workbook_path = Path(fx_workbook_path or settings.supplemental_fx_workbook)
        self._script_module = None
        self._helper_module = None
        self.downloader = FutuFinancialDownloadService()

    @property
    def available(self) -> bool:
        """Whether required script assets are available locally."""

        return self.script_path.exists() and self.helper_path.exists()

    def evaluate(self, ticker: str, company_name: str) -> dict[str, Any] | None:
        """Return a supplemental valuation payload for the given ticker."""

        if not self.available:
            return None

        try:
            run_script = self._load_script_module()
            helper = self._load_helper_module()
        except Exception as exc:
            return {"status": "skipped", "reason": f"script_import_failed: {exc}"}
        code = helper._normalize_code(ticker)
        if not run_script.has_local_financial_data(code):
            settings = get_settings()
            if settings.enable_futu_download_fallback:
                download_result = self.downloader.download(code)
                logger.info("supplemental_valuation_download_attempted", code=code, result=download_result)
                if not bool(download_result.get("success")):
                    return {"status": "skipped", "reason": f"download_failed: {download_result}"}
            if not run_script.has_local_financial_data(code):
                return None

        try:
            income_df, balance_df, cash_df, info_df = run_script.load_financial_frames(code)
            latest_period, _latest_year = run_script.latest_common_report_period(income_df, balance_df, cash_df)
            years = helper._infer_common_years(income_df, balance_df, cash_df, count=5)
            report_currency = helper._detect_report_currency(income_df, balance_df, cash_df)
        except Exception as exc:
            return {"status": "skipped", "reason": f"data_prepare_failed: {exc}"}
        if not helper._report_currency_marked(income_df, balance_df, cash_df):
            prev_ccy = helper._sheet_saved_report_currency(self.workbook_path, run_script.calc_sheet_name(code))
            if not prev_ccy and self.fx_workbook_path.exists():
                prev_ccy = helper._sheet_saved_report_currency(self.fx_workbook_path, f"{company_name}_新估值")
            report_currency = prev_ccy or run_script.fallback_report_currency(code)
        report_currency = helper._normalize_ccy(report_currency)
        quote_currency = helper._quote_currency_from_code(code)
        fx_source_book = self.workbook_path if self.workbook_path.exists() else self.fx_workbook_path
        try:
            fx_rate, fx_source = helper._resolve_fx_rate(
                workbook_path=fx_source_book,
                report_currency=report_currency,
                quote_currency=quote_currency,
                manual_fx=None,
            )
        except Exception:
            fx_rate, fx_source = self._fallback_fx_rate(report_currency, quote_currency)
            if fx_rate is None:
                return {"status": "skipped", "reason": f"fx_unavailable: {report_currency}->{quote_currency}"}

        rows, summary = helper.build_sheet_rows(
            income_df_raw=income_df,
            balance_df_raw=balance_df,
            cash_df_raw=cash_df,
            info_df=info_df,
            code=code,
            years=years,
            fx_rate=fx_rate,
            yahoo_timeout=10,
            shares_fallback_workbook=self.fx_workbook_path if self.fx_workbook_path.exists() else None,
        )
        lower_series = next((item for item in rows if item.label == "Lower Price Limit"), None)
        upper_series = next((item for item in rows if item.label == "Upper Price Limit"), None)
        if lower_series is None or upper_series is None or len(lower_series.values) == 0 or len(upper_series.values) == 0:
            return {"status": "skipped", "reason": "missing price limit rows"}

        close_price, price_source = run_script.resolve_latest_close(
            code,
            info_df,
            quote_cache=None,
            quote_source_cache=None,
            refresh_futu_on_missing=False,
        )
        lower = float(lower_series.values[-1]) * float(fx_rate)
        upper = float(upper_series.values[-1]) * float(fx_rate)
        midpoint = (lower + upper) / 2
        return {
            "status": "ok",
            "method": "legacy_value_analysis",
            "close_price": round(close_price, 2) if close_price is not None else None,
            "lower_price_limit": round(lower, 2),
            "upper_price_limit": round(upper, 2),
            "midpoint_value": round(midpoint, 2),
            "price_source": price_source,
            "report_period": latest_period or "N/A",
            "quote_currency": quote_currency,
            "report_currency": report_currency,
            "fx_rate": fx_rate,
            "fx_source": fx_source,
            "revenue_cagr_3y": run_script.calc_revenue_cagr_3y(rows),
            "summary": summary,
            "script": str(self.script_path),
        }

    def _fallback_fx_rate(self, from_ccy: str, to_ccy: str) -> tuple[float | None, str]:
        """Try to fetch a live FX rate as fallback when workbook lookup fails."""
        if from_ccy == to_ccy:
            return 1.0, "same currency"
        HARDCODED: dict[tuple[str, str], float] = {
            ("CNY", "USD"): 0.137,
            ("CNY", "HKD"): 1.07,
            ("HKD", "USD"): 0.128,
            ("USD", "CNY"): 7.3,
            ("USD", "HKD"): 7.8,
            ("HKD", "CNY"): 0.93,
            ("JPY", "USD"): 0.0067,
            ("JPY", "CNY"): 0.049,
        }
        try:
            url = f"https://open.er-api.com/v6/latest/{from_ccy}"
            resp = httpx.get(url, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                rate = data.get("rates", {}).get(to_ccy)
                if rate and rate > 0:
                    logger.info("fx_rate_fetched_live", from_ccy=from_ccy, to_ccy=to_ccy, rate=rate)
                    return float(rate), "live er-api"
        except Exception:
            pass
        pair = (from_ccy, to_ccy)
        if pair in HARDCODED:
            logger.warning("fx_rate_using_hardcoded", from_ccy=from_ccy, to_ccy=to_ccy, rate=HARDCODED[pair])
            return HARDCODED[pair], "hardcoded fallback"
        inverse = (to_ccy, from_ccy)
        if inverse in HARDCODED and HARDCODED[inverse] > 0:
            rate = 1.0 / HARDCODED[inverse]
            logger.warning("fx_rate_using_hardcoded_inverse", from_ccy=from_ccy, to_ccy=to_ccy, rate=rate)
            return rate, "hardcoded fallback (inverted)"
        return None, ""

    def _load_script_module(self):
        if self._script_module is None:
            scripts_dir = str(self.script_path.parent)
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            self._script_module = _load_module("legacy_run_value_analysis_by_code", self.script_path)
        return self._script_module

    def _load_helper_module(self):
        if self._helper_module is None:
            self._helper_module = _load_module("legacy_value_investment_market_value", self.helper_path)
        return self._helper_module
