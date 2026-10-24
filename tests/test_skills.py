"""Tests for the reusable skills (optimization task §4.1 / §4.5).

Every external dependency (Yahoo Finance, the legacy valuation program, filing
discovery, the vector store) is stubbed, so these run offline in well under a
second and assert the *contract*: uniform result envelope, caching, bounded
error handling and telemetry attribution.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import performance_tracker as perf
from app.services.compute_cache_service import cache_stats, invalidate_all
from app.skills import classify_error, retry_transient, skill_span
from app.skills.base import PermanentSkillError, TransientSkillError
from app.skills import market_data as market_data_skill
from app.skills.evidence_retrieval import EvidenceRetrievalSkill
from app.skills.financial_metrics import FinancialMetricsSkill
from app.skills.financial_report_ingestion import FinancialReportIngestionSkill
from app.skills.valuation import ValuationSkill


@pytest.fixture(autouse=True)
def _clean_caches_and_runs():
    """Skills cache process-wide and runs are process-global: isolate both."""

    invalidate_all()
    before = set(perf._RUNS)
    yield
    invalidate_all()
    for task_id in set(perf._RUNS) - before:
        perf.drop_run(task_id)
    perf.bind_run(None)


# ---------------------------------------------------------------------------
# base: telemetry seam + retry policy
# ---------------------------------------------------------------------------

def test_skill_span_accepts_span_kind_and_reports_io() -> None:
    """Regression: skills pass ``span_kind='io'`` to split download from parse."""

    run = perf.start_run("US.NVDA")
    with skill_span("market_data", span_kind="io") as record:
        record.detail = "requested=2 resolved=2"
    with skill_span("financial_metrics") as compute_record:
        compute_record.cache_hit = True
    summary = perf.finish_run(run.task_id)

    by_name = {item["skill_name"]: item for item in summary["skills"]}
    assert by_name["market_data"]["span_kind"] == "io"
    assert by_name["financial_metrics"]["span_kind"] == perf.SPAN_KIND_SKILL
    assert by_name["financial_metrics"]["cache_hit"] is True


def test_skill_span_without_active_run_still_executes() -> None:
    with skill_span("valuation", span_kind="io") as record:
        assert record.task_id == ""


def test_classify_error_splits_transient_from_permanent() -> None:
    class ReadTimeout(Exception):
        pass

    class BadParams(Exception):
        pass

    assert isinstance(classify_error(ReadTimeout("slow")), TransientSkillError)
    assert isinstance(classify_error(BadParams("nope")), PermanentSkillError)


def test_retry_transient_retries_then_succeeds() -> None:
    calls: list[int] = []

    class ReadTimeout(Exception):
        pass

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ReadTimeout("timed out")
        return "ok"

    assert retry_transient(flaky, attempts=3, backoff_seconds=0.0) == "ok"
    assert len(calls) == 3


def test_retry_transient_does_not_retry_permanent_errors() -> None:
    calls: list[int] = []

    def always_bad() -> None:
        calls.append(1)
        raise ValueError("schema mismatch")

    with pytest.raises(PermanentSkillError):
        retry_transient(always_bad, attempts=3, backoff_seconds=0.0)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# market_data skill: one shared, cached Yahoo fetch
# ---------------------------------------------------------------------------

def test_fetch_peer_market_data_dedupes_and_preserves_snapshot_defaults(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_fetch(tickers: list[str], *, force_refresh: bool = False) -> dict[str, list[dict]]:
        calls.append(list(tickers))
        return {
            "US.NVDA": [{"company_name": None, "currency": None, "revenue": 1000.0, "net_margin": 0.4}],
            "US.AMD": [{"company_name": "AMD", "currency": "USD", "revenue": 500.0}],
        }

    monkeypatch.setattr(market_data_skill, "fetch_yahoo_peer_financials", fake_fetch)

    # US.NVDA and NVDA resolve to the same Yahoo symbol -> fetched once.
    result = market_data_skill.fetch_peer_market_data(["US.NVDA", "NVDA", "US.AMD"])

    assert calls == [["US.NVDA", "US.AMD"]]
    assert result.requested == ["US.NVDA", "US.AMD"]
    assert result.resolved == ["US.NVDA", "US.AMD"]
    snapshot = result.snapshots["US.NVDA"]
    # Defaults must match what PeerAnalystNode built inline before the skill.
    assert snapshot["company_name"] == "US.NVDA"
    assert snapshot["currency"] == "USD"
    assert snapshot["revenue"] == 1000.0
    assert snapshot["net_margin"] == 0.4
    assert snapshot["data_source"] == "yahoo_finance_ttm"
    assert result.warnings == []


def test_fetch_peer_market_data_reports_unresolved_tickers(monkeypatch) -> None:
    monkeypatch.setattr(market_data_skill, "fetch_yahoo_peer_financials", lambda tickers, **_: {"US.NVDA": [{}]})

    result = market_data_skill.fetch_peer_market_data(["US.NVDA", "US.UNKNOWN"])
    assert result.unresolved == ["US.UNKNOWN"]
    assert "US.UNKNOWN" in result.warnings[0]
    assert "US.UNKNOWN" not in result.snapshots


def test_fetch_peer_market_data_empty_input_skips_network(monkeypatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("must not fetch for an empty ticker list")

    monkeypatch.setattr(market_data_skill, "fetch_yahoo_peer_financials", boom)
    result = market_data_skill.fetch_peer_market_data([])
    assert result.count == 0
    assert result.requested == []


def test_peer_market_data_cache_absorbs_the_duplicate_fetch(monkeypatch) -> None:
    """The measured baseline fetched the same peers twice per run (5.6–11.2 s).

    Caching lives in ``financial_data_provider.fetch_yahoo_peer_financials`` so
    every call site benefits; the network seam (``yfinance``) is what gets
    stubbed here, therefore the assertion is about the real cache.
    """

    import sys
    import types

    fetched: list[str] = []

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def info(self) -> dict[str, Any]:
            fetched.append(self.symbol)
            return {"totalRevenue": 1_000_000, "shortName": self.symbol, "financialCurrency": "USD"}

    class _FakeTickers:
        def __init__(self, symbols: str) -> None:
            self.tickers = {s: _FakeTicker(s) for s in symbols.split()}

    fake_yf = types.ModuleType("yfinance")
    fake_yf.Tickers = _FakeTickers  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    first = market_data_skill.fetch_peer_market_data(["US.NVDA"])
    second = market_data_skill.fetch_peer_market_data(["US.NVDA"])
    assert first.snapshots == second.snapshots
    assert first.snapshots["US.NVDA"]["revenue"] == 1_000_000
    assert fetched == ["NVDA"], f"expected the shared cache to absorb the repeat fetch, got {fetched}"

    forced = market_data_skill.fetch_peer_market_data(["US.NVDA"], force_refresh=True)
    assert fetched == ["NVDA", "NVDA"], "force_refresh must bypass the cache"
    assert forced.snapshots == first.snapshots

    stats = cache_stats()["market"]
    assert stats["hits"] >= 1
    assert stats["hit_rate"] and stats["hit_rate"] > 0




# ---------------------------------------------------------------------------
# financial_metrics skill: deterministic indicators + compute cache
# ---------------------------------------------------------------------------

REPORTS = [
    {"fiscal_year": 2023, "revenue": 100.0, "net_income": 10.0, "shares_outstanding": 10.0,
     "gross_profit": 40.0, "operating_income": 20.0, "free_cash_flow": 15.0,
     "total_equity": 80.0, "total_debt": 20.0, "total_assets": 120.0},
    {"fiscal_year": 2024, "revenue": 150.0, "net_income": 20.0, "shares_outstanding": 10.5,
     "gross_profit": 66.0, "operating_income": 33.0, "free_cash_flow": 25.0,
     "total_equity": 95.0, "total_debt": 22.0, "total_assets": 150.0},
]


class _StubFinancialService:
    """Records which FinancialService methods were actually invoked."""

    def __init__(self, reports: list[dict] | None = None, fail: bool = False) -> None:
        self.calls: list[str] = []
        self._reports = REPORTS if reports is None else reports
        self._fail = fail

    def summarize_company(self, ticker: str, company_name: str):
        self.calls.append("summarize")
        if self._fail:
            raise RuntimeError("provider unavailable")
        summary = {"years": [r["fiscal_year"] for r in self._reports], "revenue_cagr": 0.22,
                   "currency": "USD", "data_source": "provider"}
        return summary, list(self._reports), []

    def compare_peers(self, ticker, company_name, reports, peers):
        self.calls.append("compare_peers")
        return {"metrics": {"gross_margin": {"target": 0.44}}, "peers": list(peers or [])}

    def identify_red_flags(self, summary, peer_comparison):
        self.calls.append("red_flags")
        return ["share_dilution"]


def test_financial_metrics_skill_computes_and_caches() -> None:
    service = _StubFinancialService()
    skill = FinancialMetricsSkill(session=None, service=service)
    peers = [{"ticker": "US.AMD", "company_name": "AMD"}]

    first = skill.compute("US.NVDA", "NVIDIA", peers)
    assert first.status == "succeeded" and first.ok
    assert first.cache_hit is False
    assert first.years == [2023, 2024]
    assert first.currency == "USD"
    assert first.red_flags == ["share_dilution"]
    assert [row["fiscal_year"] for row in first.metrics_by_year] == [2023, 2024]
    assert service.calls.count("summarize") == 1

    second = skill.compute("US.NVDA", "NVIDIA", peers)
    assert second.cache_hit is True
    assert service.calls.count("summarize") == 1, "a cache hit must not recompute"
    assert second.metrics_by_year == first.metrics_by_year
    assert second.cache_key == first.cache_key

    forced = skill.compute("US.NVDA", "NVIDIA", peers, force_refresh=True)
    assert forced.cache_hit is False
    assert service.calls.count("summarize") == 2
    assert forced.metrics_by_year == first.metrics_by_year


def test_financial_metrics_can_skip_peer_network() -> None:
    """§6.3: structured analysis must not be blocked by peer I/O."""

    service = _StubFinancialService()
    skill = FinancialMetricsSkill(session=None, service=service)
    result = skill.compute("US.NVDA", "NVIDIA", [{"ticker": "US.AMD"}], with_peer_comparison=False)

    assert "compare_peers" not in service.calls
    assert result.peer_comparison == {}
    assert result.status == "succeeded"


def test_financial_metrics_reports_missing_values_without_filling_them() -> None:
    sparse = [{"fiscal_year": 2024, "revenue": 150.0, "net_income": None, "shares_outstanding": None}]
    skill = FinancialMetricsSkill(session=None, service=_StubFinancialService(reports=sparse))
    result = skill.compute("US.NVDA", "NVIDIA", [], with_peer_comparison=False)

    assert result.status == "succeeded"
    assert result.missing.get("net_margin") == [2024]
    assert result.provenance, "every metric must carry a provenance string"


def test_financial_metrics_failure_is_not_cached() -> None:
    service = _StubFinancialService(fail=True)
    skill = FinancialMetricsSkill(session=None, service=service)

    first = skill.compute("US.NVDA", "NVIDIA", [])
    assert first.status == "failed"
    assert first.errors and "provider unavailable" in first.errors[0]

    second = skill.compute("US.NVDA", "NVIDIA", [])
    assert second.cache_hit is False, "a failed computation must stay retryable"
    assert service.calls.count("summarize") == 2


def test_financial_metrics_requires_a_ticker() -> None:
    skill = FinancialMetricsSkill(session=None, service=_StubFinancialService())
    with pytest.raises(PermanentSkillError):
        skill.compute("", "NVIDIA", [])

def test_metrics_fingerprint_resolves_against_the_real_schema(session) -> None:
    """Regression: the fingerprint probed a non-existent ``updated_at`` column.

    ``FinancialReport`` only has ``created_at``. The AttributeError was swallowed,
    every cache key silently degraded to ``no-db``, and a newly parsed filing
    could not invalidate cached metrics inside the TTL. The performance
    benchmark caught it as a per-call debug log.
    """

    from app.db.models import FinancialReport
    from tests.helpers import load_demo_dataset

    load_demo_dataset(session)
    skill = FinancialMetricsSkill(session=session, service=_StubFinancialService())
    company_id = session.query(FinancialReport).first().company_id

    fingerprint = skill._data_fingerprint("US.NVDA")
    assert fingerprint != "no-db", "fingerprint must resolve, not fall through the except"
    count, _, stamp = fingerprint.partition(":")
    # Scoped to this company: the demo dataset holds 15 rows across 3 tickers.
    assert int(count) == session.query(FinancialReport).filter_by(company_id=company_id).count() == 5
    assert stamp not in ("", "-"), fingerprint

    assert skill._data_fingerprint("US.NOTLISTED") == "no-company"


def test_new_financial_data_invalidates_the_metrics_cache(session) -> None:
    """The fingerprint must make the cache key data-aware, not just TTL-aware."""

    from datetime import date, datetime, timezone

    from app.db.models import FinancialReport
    from tests.helpers import load_demo_dataset

    load_demo_dataset(session)
    service = _StubFinancialService()
    skill = FinancialMetricsSkill(session=session, service=service)

    assert skill.compute("US.NVDA", "NVIDIA", [], with_peer_comparison=False).cache_hit is False
    assert skill.compute("US.NVDA", "NVIDIA", [], with_peer_comparison=False).cache_hit is True
    assert service.calls.count("summarize") == 1

    company_id = session.query(FinancialReport).first().company_id
    session.add(
        FinancialReport(
            company_id=company_id,
            period_type="annual",
            fiscal_year=2030,
            fiscal_period="FY",
            report_date=date(2030, 12, 31),
            revenue=999.0,
            created_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
    )
    session.commit()

    third = skill.compute("US.NVDA", "NVIDIA", [], with_peer_comparison=False)
    assert third.cache_hit is False, "a new report row must invalidate cached metrics"
    assert service.calls.count("summarize") == 2





# ---------------------------------------------------------------------------
# valuation skill: legacy program untouched, results reused
# ---------------------------------------------------------------------------

VALUATION_PAYLOAD = {
    "blended": {"bear": 370.66, "base": 614.25, "bull": 848.3},
    "legacy_value_analysis": {"lower_price_limit": 421.65, "upper_price_limit": 967.79,
                              "midpoint_value": 694.72},
    "epv_available": True,
    "valuation_method": "epv_baseline",
    "current_price": 509.9,
    "price_vs_valuation": {"signal": "watch", "upside_pct": 20.5},
    "shares_outstanding": 9_126_000_000.0,
    "fx_rate": 1.07,
}

SUMMARY = {"years": [2023, 2024], "revenue_cagr": 0.076, "latest_metrics": {"revenue": 150.0}}


class _StubValuationService:
    def __init__(self, payload: dict | None = None, fail: bool = False) -> None:
        self.calls = 0
        self._payload = VALUATION_PAYLOAD if payload is None else payload
        self._fail = fail

    def run(self, financial_summary, peer_comparison, company_profile, *, ticker, company_name):
        self.calls += 1
        if self._fail:
            raise RuntimeError("legacy program crashed")
        return dict(self._payload), {"growth_years": 5, "terminal_multiple": 12.0}


def test_valuation_skill_passes_numbers_through_unchanged() -> None:
    service = _StubValuationService()
    skill = ValuationSkill(service=service, session=None)

    result = skill.run(SUMMARY, {}, {}, ticker="HK.00700", company_name="Tencent")
    assert service.calls == 1
    assert result.cache_hit is False
    # Buy/sell prices are read straight off the legacy payload.
    assert result.buy_price == 370.66
    assert result.base_price == 614.25
    assert result.sell_price == 848.3
    assert result.epv_lower_price_limit == 421.65
    assert result.epv_upper_price_limit == 967.79
    assert result.epv_midpoint_value == 694.72
    assert result.price_signal["signal"] == "watch"
    assert result.epv_available is True
    assert result.valuation_result == VALUATION_PAYLOAD
    # Only additive metadata may appear in assumptions.
    assert result.assumptions["growth_years"] == 5
    assert set(result.assumptions) - {"growth_years", "terminal_multiple"} == {"_cache"}
    assert result.as_state_patch() == {
        "valuation_result": result.valuation_result,
        "valuation_assumptions": result.assumptions,
    }


def test_valuation_skill_caches_identical_inputs() -> None:
    service = _StubValuationService()
    skill = ValuationSkill(service=service, session=None)

    first = skill.run(SUMMARY, {}, {}, ticker="HK.00700", company_name="Tencent")
    second = skill.run(SUMMARY, {}, {}, ticker="HK.00700", company_name="Tencent")
    assert second.cache_hit is True
    assert service.calls == 1, "the legacy program (plus FX round-trip) must not run twice"
    assert second.numeric_snapshot() == first.numeric_snapshot()

    forced = skill.run(SUMMARY, {}, {}, ticker="HK.00700", company_name="Tencent", force_refresh=True)
    assert forced.cache_hit is False
    assert service.calls == 2
    assert forced.numeric_snapshot() == first.numeric_snapshot()


def test_valuation_cache_key_reacts_to_input_changes() -> None:
    skill = ValuationSkill(service=_StubValuationService(), session=None)
    base = skill.cache_key_for("HK.00700", SUMMARY, {}, {})

    assert skill.cache_key_for("HK.00700", SUMMARY, {}, {}) == base
    assert skill.cache_key_for("HK.00700", {**SUMMARY, "revenue_cagr": 0.09}, {}, {}) != base
    assert skill.cache_key_for("US.NVDA", SUMMARY, {}, {}) != base
    # A different valuation date must not reuse the previous day's numbers.
    assert skill.cache_key_for("HK.00700", SUMMARY, {}, {}, valuation_date="2020-01-01") != base


def test_valuation_failure_is_not_cached_and_reports_error() -> None:
    service = _StubValuationService(fail=True)
    skill = ValuationSkill(service=service, session=None)

    first = skill.run(SUMMARY, {}, {}, ticker="HK.00700")
    assert first.valuation_result == {}
    assert first.errors
    second = skill.run(SUMMARY, {}, {}, ticker="HK.00700")
    assert second.cache_hit is False
    assert service.calls == 2


def test_valuation_skill_requires_a_ticker() -> None:
    skill = ValuationSkill(service=_StubValuationService(), session=None)
    with pytest.raises(PermanentSkillError):
        skill.run(SUMMARY, {}, {})



# ---------------------------------------------------------------------------
# financial_report_ingestion skill: dedupe, retries, index/no-index split
# ---------------------------------------------------------------------------

class _StubFilingService:
    """Stands in for FilingService; records how it was invoked."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self._error = error

    def ingest_company_filings(self, *, session, company, market, years, filing_types):
        self.calls.append((company.ticker, market, years, tuple(sorted(filing_types or []))))
        if self._error is not None:
            raise self._error
        return [], []

    def rebuild_local_filing_index(self, session, company, filing_types):
        self.calls.append(("rebuild", company.ticker))
        return {"processed": 0, "skipped": 0}


def test_ingestion_skill_uses_the_no_index_service_for_peers(session) -> None:
    indexed = _StubFilingService()
    no_index = _StubFilingService()
    skill = FinancialReportIngestionSkill(filing_service=indexed, no_index_filing_service=no_index)

    target = skill.ingest(session, "NVDA", "NVIDIA", years=1, filing_types={"annual_report"})
    assert target.ticker == "US.NVDA"  # normalized
    assert target.status == "succeeded"
    assert target.index_vectors is True
    assert indexed.calls and not no_index.calls
    assert target.counts() == {"available": 0, "downloaded": 0, "reused": 0, "parsed": 0, "indexed": 0, "failed": 0}

    peer = skill.ingest(session, "AMD", "AMD", years=1, filing_types={"annual_report"}, index_vectors=False)
    assert peer.index_vectors is False
    assert no_index.calls and len(indexed.calls) == 1
    assert peer.to_dict()["ticker"] == "US.AMD"


def test_ingestion_skill_dedupes_tickers_across_a_batch(session) -> None:
    service = _StubFilingService()
    skill = FinancialReportIngestionSkill(filing_service=service, no_index_filing_service=service)

    results = skill.ingest_many(session, ["NVDA", "US.NVDA", "AMD"], years=1, filing_types={"annual_report"})
    assert sorted(results) == ["US.AMD", "US.NVDA"]
    assert len(service.calls) == 2, f"alias tickers must collapse to one fetch, got {service.calls}"


def test_ingestion_skill_force_refresh_rebuilds_from_local_files(session) -> None:
    service = _StubFilingService()
    skill = FinancialReportIngestionSkill(filing_service=service, no_index_filing_service=service)

    skill.ingest(session, "NVDA", "NVIDIA", years=1, filing_types={"annual_report"}, force_refresh=True)
    assert ("rebuild", "US.NVDA") in service.calls


def test_ingestion_skill_reports_failures_instead_of_raising(session) -> None:
    class ReadTimeout(Exception):
        pass

    service = _StubFilingService(error=ReadTimeout("network down"))
    skill = FinancialReportIngestionSkill(filing_service=service, no_index_filing_service=service)

    results = skill.ingest_many(session, ["NVDA"], years=1, filing_types={"annual_report"})
    result = results["US.NVDA"]
    assert result.status == "failed"
    assert result.failed_tasks and result.failed_tasks[0]["ticker"] == "US.NVDA"
    assert result.warnings
    # A transient error is retried within the bounded budget (attempts=2).
    assert len(service.calls) == 2


def test_ingestion_skill_requires_a_ticker(session) -> None:
    skill = FinancialReportIngestionSkill(filing_service=_StubFilingService())
    with pytest.raises(ValueError):
        skill.ingest(session, "", "No Ticker")



# ---------------------------------------------------------------------------
# evidence_retrieval skill: one memoized lookup, vector then keyword fallback
# ---------------------------------------------------------------------------

HITS = [
    {"id": "c1", "filing_id": 11, "section": "Item 1. Business", "page_number": 3, "score": 0.82,
     "text": "CUDA moat", "metadata": {"fiscal_year": 2024, "filing_type": "annual_report", "ticker": "US.NVDA"}},
    {"id": "c2", "filing_id": 11, "section": "Item 1A. Risk Factors", "page_number": 9, "score": 0.64,
     "text": "concentration risk", "metadata": {"fiscal_year": 2024, "filing_type": "annual_report",
                                                "ticker": "US.NVDA"}},
]


class _StubRetrievalService:
    def __init__(self, hits: list[dict] | None = None, error: Exception | None = None) -> None:
        self.hits = HITS if hits is None else hits
        self.error = error
        self.calls: list[tuple] = []

    def search(self, query: str, filters: dict | None = None, top_k: int = 4) -> list[dict]:
        self.calls.append((query, dict(filters or {}), top_k))
        if self.error is not None:
            raise self.error
        return list(self.hits)


def test_evidence_retrieval_dedupes_and_builds_citations(session) -> None:
    retrieval = _StubRetrievalService()
    skill = EvidenceRetrievalSkill(retrieval_service=retrieval)

    result = skill.retrieve(session, 1, area="moat", queries=["moat", "competitive position"], top_k=2)
    assert result.source == "vector"
    # Same filing_id+section from the second query must not be added twice.
    assert result.count == 2
    assert len(retrieval.calls) == 2
    assert retrieval.calls[0][1] == {"company_id": 1, "filing_type": "annual_report"}

    citations = result.citations()
    assert {c["chunk_id"] for c in citations} == {"c1", "c2"}
    first = citations[0]
    assert first["filing_id"] == 11
    assert first["section"] == "Item 1. Business"
    assert first["page_number"] == 3
    assert first["fiscal_year"] == 2024
    assert first["filing_type"] == "annual_report"
    assert first["ticker"] == "US.NVDA"
    assert first["source"] == "vector"


def test_evidence_retrieval_caches_identical_area_and_queries(session) -> None:
    retrieval = _StubRetrievalService()
    skill = EvidenceRetrievalSkill(retrieval_service=retrieval)

    first = skill.retrieve(session, 1, area="moat", queries=["moat"])
    assert first.cache_hit is False
    second = skill.retrieve(session, 1, area="moat", queries=["moat"])
    assert second.cache_hit is True
    assert second.hits == first.hits
    assert len(retrieval.calls) == 1, "a cached lookup must not hit the vector store again"

    # A different analysis area is a different cache entry.
    other = skill.retrieve(session, 1, area="risks", queries=["moat"])
    assert other.cache_hit is False
    assert len(retrieval.calls) == 2


def test_evidence_retrieval_without_queries_does_no_work(session) -> None:
    retrieval = _StubRetrievalService()
    skill = EvidenceRetrievalSkill(retrieval_service=retrieval)

    result = skill.retrieve(session, 1, area="moat", queries=[], keywords=[])
    assert result.source == "none"
    assert result.count == 0
    assert retrieval.calls == []


def test_evidence_retrieval_falls_back_to_keyword_search(session, monkeypatch) -> None:
    retrieval = _StubRetrievalService(hits=[])
    skill = EvidenceRetrievalSkill(retrieval_service=retrieval)
    keyword_rows = [{"id": "k1", "filing_id": 12, "section": "MD&A", "page_number": 20, "score": None,
                     "metadata": {"fiscal_year": 2023}}]
    monkeypatch.setattr(
        EvidenceRetrievalSkill, "keyword_search", staticmethod(lambda *a, **k: list(keyword_rows))
    )

    result = skill.retrieve(session, 1, area="moat", queries=["moat"], keywords=["moat", "CUDA"])
    assert result.source == "keyword"
    assert result.count == 1
    assert result.citations()[0]["chunk_id"] == "k1"


def test_evidence_retrieval_survives_a_vector_store_failure(session, monkeypatch) -> None:
    retrieval = _StubRetrievalService(error=RuntimeError("chroma unavailable"))
    skill = EvidenceRetrievalSkill(retrieval_service=retrieval)
    monkeypatch.setattr(
        EvidenceRetrievalSkill,
        "keyword_search",
        staticmethod(lambda *a, **k: [{"id": "k2", "filing_id": 13, "section": "Business", "metadata": {}}]),
    )

    result = skill.retrieve(session, 1, area="moat", queries=["moat"], keywords=["moat"])
    assert result.source == "keyword"
    assert result.count == 1


def test_evidence_retrieval_can_skip_the_vector_store_entirely(session, monkeypatch) -> None:
    """§9: a structured-only analysis must never touch the vector store."""

    retrieval = _StubRetrievalService()
    skill = EvidenceRetrievalSkill(retrieval_service=retrieval)
    monkeypatch.setattr(
        EvidenceRetrievalSkill,
        "keyword_search",
        staticmethod(lambda *a, **k: [{"id": "k3", "filing_id": 14, "section": "Notes", "metadata": {}}]),
    )

    result = skill.retrieve(session, 1, area="financials", keywords=["revenue"], use_vector=False)
    assert retrieval.calls == []
    assert result.source == "keyword"
    assert result.count == 1



# ---------------------------------------------------------------------------
# node contract: DataFetcherNode <-> financial_report_ingestion skill
# ---------------------------------------------------------------------------

def test_data_fetcher_return_contract_and_state_isolation(session) -> None:
    """Regression: the node must still return ``data_availability`` (post-fetch)."""

    from app.graph.nodes.data_fetcher import DataFetcherNode
    from app.graph.state import create_initial_state
    from tests.helpers import load_demo_dataset

    load_demo_dataset(session)
    node = DataFetcherNode(session)
    availability = {
        "fetch_needed": {},
        "target": {"filings": {"status": "missing"}},
        "news": {"status": "stale"},
    }
    state = create_initial_state("NVDA", "NVIDIA")
    state["data_availability"] = availability
    state["research_plan"] = {"sections": {}}

    result = node(state)
    assert set(result) == {"fetch_results", "data_availability", "warnings"}
    # Nothing was needed, so the refreshed view equals the incoming one...
    assert result["data_availability"] == availability
    # ...but it must be a copy: state snapshots are shared with parallel branches.
    assert result["data_availability"] is not availability
    assert result["fetch_results"]["target_filings_fetched"] is False
    assert result["fetch_results"]["peer_filings_fetched"] == []
    assert result["fetch_results"]["news_fetched"] is False


def test_refresh_availability_marks_only_what_was_fetched() -> None:
    from app.graph.nodes.data_fetcher import DataFetcherNode

    availability = {
        "fetch_needed": {"target_filings": True, "peer_filings": ["US.AMD", "US.INTC"], "news": True},
        "target": {"filings": {"status": "missing", "effective_count": 0}},
        "peers": {"US.AMD": {"filings": {"status": "missing"}}},
        "news": {"status": "stale", "article_count": 0},
    }
    fetched = {
        "target_filings_fetched": True,
        "peer_filings_fetched": ["US.AMD"],  # US.INTC failed -> stays pending
        "news_fetched": False,
        "fetch_errors": ["Peer filings fetch failed for US.INTC"],
    }

    updated = DataFetcherNode._refresh_availability(availability, fetched)
    assert updated["target"]["filings"]["status"] == "available"
    assert updated["peers"]["US.AMD"]["filings"]["status"] == "available"
    assert updated["news"]["status"] == "stale", "news was not fetched, so it stays stale"
    assert updated["fetch_needed"] == {"target_filings": False, "peer_filings": ["US.INTC"], "news": True}
    # The caller's dict is never mutated in place.
    assert availability["target"]["filings"]["status"] == "missing"
    assert availability["fetch_needed"]["peer_filings"] == ["US.AMD", "US.INTC"]


def test_data_fetcher_delegates_to_the_ingestion_skill(session) -> None:
    from app.graph.nodes.data_fetcher import DataFetcherNode
    from app.graph.state import create_initial_state
    from app.skills.financial_report_ingestion import IngestionResult
    from tests.helpers import load_demo_dataset

    class _FakeSkill:
        def __init__(self) -> None:
            self.ingest_calls: list[dict] = []
            self.batch_calls: list[dict] = []

        def ingest(self, db, ticker, company_name, **kwargs):
            self.ingest_calls.append({"ticker": ticker, **kwargs})
            return IngestionResult(ticker=ticker, status="succeeded",
                                   available_filings=[{"id": 1, "filing_type": "annual_report"}])

        def ingest_many(self, db, tickers, **kwargs):
            self.batch_calls.append({"tickers": list(tickers), **kwargs})
            return {t: IngestionResult(ticker=t, status="succeeded") for t in tickers}

    load_demo_dataset(session)
    node = DataFetcherNode(session)
    fake = _FakeSkill()
    node.skill = fake

    state = create_initial_state("NVDA", "NVIDIA")
    state["force_refresh"] = True
    state["research_plan"] = {"sections": {}}
    state["data_availability"] = {
        "fetch_needed": {"target_filings": True, "peer_filings": ["US.AMD"], "news": False},
        "target": {"filings": {"status": "missing"}},
        "peers": {},
        "news": {"status": "stale"},
    }

    result = node(state)
    fetched = result["fetch_results"]
    assert fetched["target_filings_fetched"] is True
    assert fetched["peer_filings_fetched"] == ["US.AMD"]
    assert fetched["news_fetched"] is False
    assert fetched["ingestion"]["US.NVDA"]["counts"]["available"] == 1
    assert fetched["ingestion"]["US.AMD"]["status"] == "succeeded"

    # Target ingestion indexes vectors; peer ingestion must not (existing split).
    assert fake.ingest_calls[0]["index_vectors"] is True
    assert fake.ingest_calls[0]["force_refresh"] is True, "force_refresh must reach the skill"
    assert fake.batch_calls[0]["index_vectors"] is False

    availability = result["data_availability"]
    assert availability["target"]["filings"]["status"] == "available"
    assert availability["peers"]["US.AMD"]["filings"]["status"] == "available"
    assert availability["fetch_needed"]["target_filings"] is False
    assert availability["fetch_needed"]["peer_filings"] == []

