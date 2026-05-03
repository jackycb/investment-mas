"""Tests for Futu financial downloader wrapper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.services.futu_financial_download_service import FutuFinancialDownloadService


class _CompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_futu_download_service_requires_statement_triplet(tmp_path: Path) -> None:
    script = tmp_path / "download_financial_data.py"
    script.write_text("print('ok')", encoding="utf-8")
    (tmp_path / "US.NVDA_info.csv").write_text("ticker,US.NVDA\n", encoding="utf-8")

    service = FutuFinancialDownloadService(script_path=str(script), base_dir=str(tmp_path), timeout_seconds=5)

    with patch("app.services.futu_financial_download_service.subprocess.run", return_value=_CompletedProcess()):
        result = service.download("US.NVDA")

    assert result["success"] is False
    assert len(result["missing_files"]) == 3
    assert any(str(path).endswith("US.NVDA_income_year.csv") for path in result["missing_files"])


def test_futu_download_service_keeps_local_driver_enabled_for_script_resolution(tmp_path: Path) -> None:
    script = tmp_path / "download_financial_data.py"
    script.write_text("print('ok')", encoding="utf-8")
    service = FutuFinancialDownloadService(script_path=str(script), base_dir=str(tmp_path), timeout_seconds=5)

    with patch("app.services.futu_financial_download_service.subprocess.run", return_value=_CompletedProcess()):
        result = service.download("US.NVDA")

    assert result["disable_local_chromedriver"] is False


def test_futu_download_service_includes_profile_dir_in_result(tmp_path: Path) -> None:
    script = tmp_path / "download_financial_data.py"
    script.write_text("print('ok')", encoding="utf-8")
    service = FutuFinancialDownloadService(script_path=str(script), base_dir=str(tmp_path), timeout_seconds=5)

    with patch("app.services.futu_financial_download_service.subprocess.run", return_value=_CompletedProcess()):
        result = service.download("US.NVDA")

    assert "profile_dir" in result


def test_futu_download_service_prefers_local_driver_by_default(tmp_path: Path) -> None:
    script = tmp_path / "download_financial_data.py"
    script.write_text("print('ok')", encoding="utf-8")
    service = FutuFinancialDownloadService(script_path=str(script), base_dir=str(tmp_path), timeout_seconds=5)

    with patch("app.services.futu_financial_download_service.subprocess.run", return_value=_CompletedProcess()):
        result = service.download("US.NVDA")

    assert result["disable_local_chromedriver"] is False
