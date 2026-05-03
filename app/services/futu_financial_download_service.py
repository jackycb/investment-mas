"""Wrapper around the bundled Futu financial downloader script."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from app.core.config import PROJECT_ROOT, get_settings


class FutuFinancialDownloadService:
    """Download missing financial statements through the bundled Futu script."""

    def __init__(
        self,
        script_path: str | None = None,
        base_dir: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.script_path = Path(script_path or settings.futu_downloader_script_path)
        self.base_dir = Path(base_dir or settings.local_financial_base_dir)
        self.timeout_seconds = timeout_seconds or settings.futu_download_timeout_seconds

    def download(self, ticker: str) -> dict[str, object]:
        """Attempt to download annual financial data for a ticker."""

        if not self.script_path.exists():
            return {"success": False, "reason": f"script not found: {self.script_path}"}

        env = os.environ.copy()
        env.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("USE_LOCAL_CHROMEDRIVER", "1")
        if not env.get("CHROME_BINARY"):
            chrome_candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]
            for candidate in chrome_candidates:
                if Path(candidate).exists():
                    env["CHROME_BINARY"] = candidate
                    break
        if env.get("USE_LOCAL_CHROMEDRIVER", "").strip().lower() not in {"1", "true", "yes", "on"}:
            env["DISABLE_LOCAL_CHROMEDRIVER"] = "1"
        command = [sys.executable, str(self.script_path), ticker.upper()]
        try:
            proc = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "reason": f"timeout after {self.timeout_seconds}s"}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}

        ticker_upper = ticker.upper()
        files = sorted(self.base_dir.glob(f"{ticker_upper}_*"))
        expected_files = [
            self.base_dir / f"{ticker_upper}_income_year.csv",
            self.base_dir / f"{ticker_upper}_balance_year.csv",
            self.base_dir / f"{ticker_upper}_cash_year.csv",
        ]
        missing_files = [str(path) for path in expected_files if not path.exists()]
        output_lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        error_lines = [line for line in (proc.stderr or "").splitlines() if line.strip()]
        success = proc.returncode == 0 and not missing_files
        return {
            "success": success,
            "returncode": proc.returncode,
            "stdout_tail": output_lines[-8:],
            "stderr_tail": error_lines[-8:],
            "file_count": len(files),
            "expected_files": [str(path) for path in expected_files],
            "missing_files": missing_files,
            "script_path": str(self.script_path),
            "disable_local_chromedriver": env.get("DISABLE_LOCAL_CHROMEDRIVER") == "1",
            "profile_dir": env.get("CHROME_USER_DATA_DIR"),
        }
