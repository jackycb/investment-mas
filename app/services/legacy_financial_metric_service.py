"""Supplement missing profitability metrics using the legacy Futu analysis logic."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class LegacyFinancialMetricService:
    """Use the bundled legacy analysis script to compute profitability metrics."""

    def __init__(self, project_root: Path | None = None, plot_enabled: bool | None = None) -> None:
        self.project_root = project_root or PROJECT_ROOT
        self.plot_enabled = settings.legacy_financial_plot_enabled if plot_enabled is None else plot_enabled

    def compute(self, ticker: str) -> dict[str, Any] | None:
        """Return annual profitability metrics for a ticker when available."""

        try:
            from scripts.stock_analysis_futu import financial_analysis
        except Exception as exc:
            logger.warning("legacy_financial_import_failed", ticker=ticker, error=str(exc))
            return None

        try:
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                _, details = financial_analysis(
                    self.project_root,
                    ticker,
                    show_plot=self.plot_enabled,
                    max_years=10,
                    name_map=None,
                    live_quotes={},
                    write_artifacts=False,
                    return_details=True,
                )
        except Exception as exc:
            logger.warning("legacy_financial_compute_failed", ticker=ticker, error=str(exc))
            return None

        captured_output = output.getvalue().strip()
        if captured_output:
            logger.info("legacy_financial_compute_output", ticker=ticker, output=captured_output)

        averages = details.get("averages", {}) if isinstance(details, dict) else {}
        return {
            "annual_metrics": details.get("annual_metrics", []) if isinstance(details, dict) else [],
            "averages": averages,
            "ticker": ticker.upper(),
            "source": "legacy_stock_analysis_futu",
        }
