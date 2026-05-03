"""Local financial provider tests."""

from pathlib import Path

from app.core.config import get_settings
from app.providers.financial_data_provider import LocalFinancialDataProvider


def test_local_financial_provider_parses_statement_triplet(tmp_path: Path) -> None:
    (tmp_path / "US.TEST_info.csv").write_text("股票代码,US.TEST\n股票名称,Test Corp\n总股本,10亿\n", encoding="utf-8")
    (tmp_path / "US.TEST_income_year.csv").write_text(
        ",2024/12/31,2023/12/31\n營業收入,100億,80億\n毛利,60億,48億\n營業利潤,30億,20億\n除稅後利潤,25億,16億\n",
        encoding="utf-8",
    )
    (tmp_path / "US.TEST_balance_year.csv").write_text(
        ",2024/12/31,2023/12/31\n資產總計,200億,180億\n負債總計,70億,68億\n股東權益總計,130億,112億\n",
        encoding="utf-8",
    )
    (tmp_path / "US.TEST_cash_year.csv").write_text(
        ",2024/12/31,2023/12/31\n經營活動現金淨額,28億,18億\n",
        encoding="utf-8",
    )

    provider = LocalFinancialDataProvider(base_dir=str(tmp_path))
    rows = provider.load_company_financials("US.TEST", "Test Corp", years=5)

    assert len(rows) == 2
    assert rows[0]["fiscal_year"] == 2024
    assert rows[0]["revenue"] == 100 * 1e8
    assert rows[0]["gross_margin"] == 0.6
    assert rows[0]["roe"] == (25 * 1e8) / (130 * 1e8)
    assert rows[0]["shares_outstanding"] == 10 * 1e8


def test_local_financial_provider_returns_empty_for_missing_ticker(tmp_path: Path) -> None:
    provider = LocalFinancialDataProvider(base_dir=str(tmp_path))
    assert provider.load_company_financials("US.NONE", "Missing", years=5) == []


def test_local_financial_provider_attempts_download_when_only_info_exists(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "HK.00883_info.csv").write_text("股票代码,HK.00883\n股票名称,CNOOC\n", encoding="utf-8")
    monkeypatch.setenv("INVESTMENT_MAS_LOCAL_FINANCIAL_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTMENT_MAS_ENABLE_FUTU_DOWNLOAD_FALLBACK", "true")
    get_settings.cache_clear()
    provider = LocalFinancialDataProvider(base_dir=str(tmp_path))
    called: list[str] = []

    def fake_download(ticker: str) -> dict[str, object]:
        called.append(ticker)
        return {"success": False}

    provider.downloader.download = fake_download  # type: ignore[method-assign]
    rows = provider.load_company_financials("HK.00883", "CNOOC", years=5)
    assert rows == []
    assert called == ["HK.00883"]


def test_local_financial_provider_peer_load_does_not_attempt_download(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "HK.00386_info.csv").write_text("股票代码,HK.00386\n股票名称,Sinopec\n", encoding="utf-8")
    monkeypatch.setenv("INVESTMENT_MAS_LOCAL_FINANCIAL_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTMENT_MAS_ENABLE_FUTU_DOWNLOAD_FALLBACK", "true")
    get_settings.cache_clear()
    provider = LocalFinancialDataProvider(base_dir=str(tmp_path))
    called: list[str] = []

    def fake_download(ticker: str) -> dict[str, object]:
        called.append(ticker)
        return {"success": False}

    provider.downloader.download = fake_download  # type: ignore[method-assign]
    rows = provider.load_peer_financials(["HK.00386"], years=1)
    assert rows == {"HK.00386": []}
    assert called == []
