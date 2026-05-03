"""Tests for FinancialService."""

from app.services.financial_service import FinancialService


class FakeProvider:
    """Minimal in-memory provider for testing."""

    def __init__(self, data: dict[str, list[dict]]):
        self._data = data

    def load_company_financials(self, ticker: str, company_name: str, years: int = 5) -> list[dict]:
        return self._data.get(ticker, [])[:years]

    def load_peer_financials(self, peer_tickers: list[str], years: int = 5) -> dict[str, list[dict]]:
        return {ticker: self._data.get(ticker, [])[:years] for ticker in peer_tickers}


class FakeLegacyMetricService:
    """Return supplemental profitability metrics for missing fields."""

    def __init__(self, annual_metrics: list[dict] | None = None):
        self.annual_metrics = annual_metrics or []

    def compute(self, ticker: str) -> dict:
        return {"annual_metrics": self.annual_metrics, "source": "legacy_stock_analysis_futu", "ticker": ticker}


def _make_records(years: list[int], revenue_start: float = 100_000.0) -> list[dict]:
    """Generate simplified financial records for testing."""

    records: list[dict] = []
    for index, year in enumerate(years):
        revenue = revenue_start * (1.1**index)
        records.append(
            {
                "fiscal_year": year,
                "revenue": revenue,
                "gross_profit": revenue * 0.6,
                "net_income": revenue * 0.2,
                "operating_cash_flow": revenue * 0.22,
                "free_cash_flow": revenue * 0.18,
                "total_equity": revenue * 2,
                "gross_margin": 0.6,
                "net_margin": 0.2,
                "roe": 0.1,
                "roic": 0.09,
            }
        )
    return records


def test_summarize_company_happy_path_returns_summary_with_cagr() -> None:
    service = FinancialService(provider=FakeProvider({"NVDA": _make_records([2024, 2023, 2022, 2021, 2020])}))
    summary, reports, warnings = service.summarize_company("NVDA", "NVIDIA")
    assert "revenue_cagr" in summary
    assert summary["revenue_cagr"] is not None
    assert len(reports) == 5
    assert warnings == []


def test_summarize_company_empty_data_returns_empty_summary() -> None:
    service = FinancialService(provider=FakeProvider({}))
    summary, reports, warnings = service.summarize_company("UNKNOWN", "Unknown Corp")
    assert reports == []
    assert warnings
    assert summary["years"] == []


def test_summarize_company_single_year_no_cagr_crash() -> None:
    service = FinancialService(provider=FakeProvider({"X": _make_records([2024])}))
    summary, reports, warnings = service.summarize_company("X", "X Corp")
    assert len(reports) == 1
    assert summary["revenue_cagr"] is None


def test_identify_red_flags_declining_revenue_flagged() -> None:
    service = FinancialService(provider=FakeProvider({}))
    summary = {
        "revenue_cagr": 0.01,
        "gross_margin_trend": [{"value": 0.60}, {"value": 0.56}, {"value": 0.50}],
        "ocf_vs_net_income": [{"ratio": 0.8}, {"ratio": 0.75}, {"ratio": 0.7}],
    }
    flags = service.identify_red_flags(summary, {})
    assert flags


def test_identify_red_flags_healthy_company_no_flags() -> None:
    service = FinancialService(provider=FakeProvider({}))
    summary = {
        "revenue_cagr": 0.20,
        "gross_margin_trend": [{"value": 0.60}, {"value": 0.62}, {"value": 0.63}],
        "ocf_vs_net_income": [{"ratio": 1.1}, {"ratio": 1.05}, {"ratio": 1.08}],
    }
    flags = service.identify_red_flags(summary, {})
    assert flags == []


def test_compare_peers_includes_avg_and_median() -> None:
    data = {
        "NVDA": _make_records([2024]),
        "AMD": _make_records([2024], revenue_start=80_000),
        "AVGO": _make_records([2024], revenue_start=120_000),
    }
    service = FinancialService(provider=FakeProvider(data))
    _, reports, _ = service.summarize_company("NVDA", "NVIDIA")
    peers = [{"ticker": "AMD", "company_name": "AMD"}, {"ticker": "AVGO", "company_name": "Broadcom"}]
    comparison = service.compare_peers("NVDA", "NVIDIA", reports, peers)
    assert comparison["metrics"]["gross_margin"]["peer_avg"] is not None
    assert comparison["metrics"]["gross_margin"]["peer_median"] is not None


def test_compare_peers_empty_peers_no_crash() -> None:
    service = FinancialService(provider=FakeProvider({"NVDA": _make_records([2024])}))
    _, reports, _ = service.summarize_company("NVDA", "NVIDIA")
    comparison = service.compare_peers("NVDA", "NVIDIA", reports, [])
    assert isinstance(comparison, dict)


def test_summarize_company_enriches_missing_metrics_from_legacy_service() -> None:
    provider = FakeProvider(
        {
            "SH.601857": [
                {
                    "fiscal_year": 2025,
                    "revenue": 100.0,
                    "net_income": 10.0,
                    "operating_cash_flow": 11.0,
                    "free_cash_flow": 9.0,
                    "total_equity": 100.0,
                }
            ]
        }
    )
    legacy = FakeLegacyMetricService(
        annual_metrics=[
            {
                "fiscal_year": 2025,
                "gross_margin": 0.23,
                "net_margin": 0.10,
                "roe": 0.11,
                "roic": 0.09,
            }
        ]
    )
    service = FinancialService(provider=provider, legacy_metric_service=legacy)

    summary, reports, warnings = service.summarize_company("SH.601857", "PetroChina")

    assert reports[0]["gross_margin"] == 0.23
    assert summary["latest_metrics"]["roic"] == 0.09
    assert summary["roic_trend"][0]["value"] == 0.09
    assert warnings == []


def test_summarize_company_uses_latest_available_profitability_metrics() -> None:
    provider = FakeProvider(
        {
            "HK.09961": [
                {
                    "fiscal_year": 2025,
                    "revenue": 120.0,
                    "gross_margin": 0.80,
                    "total_equity": 200.0,
                },
                {
                    "fiscal_year": 2024,
                    "revenue": 100.0,
                    "gross_margin": 0.81,
                    "total_equity": 180.0,
                },
            ]
        }
    )
    legacy = FakeLegacyMetricService(
        annual_metrics=[
            {
                "fiscal_year": 2024,
                "net_margin": 0.323,
                "roe": 0.131,
                "roic": 0.056,
            }
        ]
    )
    service = FinancialService(provider=provider, legacy_metric_service=legacy)

    summary, reports, warnings = service.summarize_company("HK.09961", "Trip.com")

    assert summary["latest_metrics"]["revenue"] == 120.0
    assert summary["latest_metrics"]["net_margin"] == 0.323
    assert summary["latest_metrics"]["roe"] == 0.131
    assert summary["latest_metrics"]["roic"] == 0.056
    assert summary["latest_metric_years"]["revenue"] == 2025
    assert summary["latest_metric_years"]["roe"] == 2024
    assert reports[0]["net_income"] == 32.300000000000004
    assert not any("net_margin" in warning and "unavailable" in warning for warning in warnings)
