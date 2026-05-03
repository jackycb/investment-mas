from __future__ import annotations

import importlib.util
from io import BytesIO
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch


def _load_script_module():
    script_path = Path("/Users/jacky/Ai-code/investment-mas/scripts/download_financial_data.py")
    spec = importlib.util.spec_from_file_location("download_financial_data_script", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_chromedriver_path_prefers_managed_matching_driver(tmp_path: Path) -> None:
    module = _load_script_module()
    managed_binary = tmp_path / "mac-arm64" / "147.0.7727.103" / "chromedriver-mac-arm64" / "chromedriver"
    managed_binary.parent.mkdir(parents=True, exist_ok=True)
    managed_binary.write_text("driver", encoding="utf-8")

    with (
        patch.object(module, "MANAGED_CHROMEDRIVER_DIR", tmp_path),
        patch.object(module, "_resolve_chrome_version", return_value="147.0.7727.103"),
        patch.object(module, "_chromedriver_platform_label", return_value="mac-arm64"),
        patch.object(module, "_binary_major_version", side_effect=lambda path: 147 if path == str(managed_binary) else 145 if path else None),
        patch.dict(module.os.environ, {}, clear=True),
    ):
        resolved = module._resolve_chromedriver_path()

    assert resolved == str(managed_binary)


def test_download_managed_chromedriver_extracts_binary(tmp_path: Path) -> None:
    module = _load_script_module()

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("chromedriver-mac-arm64/chromedriver", "binary")
    zip_payload = zip_buffer.getvalue()

    class _FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    def _fake_urlopen(_url: str, timeout: int = 30):
        return _FakeResponse(zip_payload)

    with (
        patch.object(module, "MANAGED_CHROMEDRIVER_DIR", tmp_path),
        patch.object(module, "_chromedriver_platform_label", return_value="mac-arm64"),
        patch.object(module.urllib.request, "urlopen", side_effect=_fake_urlopen),
    ):
        binary_path = module._download_managed_chromedriver("147.0.7727.103")

    binary = Path(binary_path)
    assert binary.exists()
    assert binary.name == "chromedriver"


def test_build_chrome_options_interactive_login_keeps_images_enabled() -> None:
    module = _load_script_module()

    with (
        patch.object(module, "_resolve_chrome_binary", return_value=""),
        patch.dict(module.os.environ, {"FUTU_INTERACTIVE_LOGIN": "1"}, clear=True),
    ):
        options = module._build_chrome_options("off")

    args = options.arguments
    prefs = options.experimental_options["prefs"]
    assert "--disable-background-networking" not in args
    assert "--blink-settings=imagesEnabled=false" not in args
    assert prefs["download.default_directory"]


def test_build_chrome_options_download_mode_uses_lightweight_flags() -> None:
    module = _load_script_module()

    with (
        patch.object(module, "_resolve_chrome_binary", return_value=""),
        patch.dict(module.os.environ, {}, clear=True),
    ):
        options = module._build_chrome_options("new")

    args = options.arguments
    prefs = options.experimental_options["prefs"]
    assert "--disable-background-networking" in args
    assert "--blink-settings=imagesEnabled=false" in args
    assert prefs["profile.default_content_setting_values.images"] == 2


def test_mark_login_session_ready_writes_state_file(tmp_path: Path) -> None:
    module = _load_script_module()

    with (
        patch.object(module, "DEFAULT_FUTU_PROFILE_DIR", tmp_path),
        patch.object(module, "FUTU_LOGIN_STATE_FILE", tmp_path / ".login_ready.json"),
        patch.dict(module.os.environ, {"CHROME_USER_DATA_DIR": str(tmp_path)}, clear=True),
    ):
        module._mark_login_session_ready()

    payload = (tmp_path / ".login_ready.json").read_text(encoding="utf-8")
    assert "manual_login_then_headless" in payload
    assert str(tmp_path) in payload


def test_main_manual_login_returns_before_download(tmp_path: Path, capsys) -> None:
    module = _load_script_module()

    with (
        patch.object(module, "_ensure_runtime_env_defaults"),
        patch.object(module, "_manual_login_once"),
        patch.object(module, "normalize_code", return_value="US.NVDA"),
        patch.object(module, "getStockInfoWeb") as get_info,
        patch.object(module, "downloadFinancialDataFutu") as download_financials,
        patch.object(sys, "argv", ["download_financial_data.py", "US.NVDA", "--manual-login"]),
    ):
        module.main()

    get_info.assert_not_called()
    download_financials.assert_not_called()
    assert "Login session saved for US.NVDA" in capsys.readouterr().out
