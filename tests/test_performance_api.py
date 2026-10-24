"""Tests for lightweight performance telemetry and its API surface (§10).

These cover the measured answers required by the optimization task: total
runtime, slowest nodes, whether specialists really overlapped, cache hits,
LLM call counts and how much a repeat run saved.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.models import NodeExecution
from app.services import performance_tracker as perf

BASE = datetime(2026, 1, 1, 0, 0, 0)


def _add_span(
    session: Session,
    task_id: str,
    node_name: str,
    *,
    ticker: str = "NVDA",
    span_kind: str = perf.SPAN_KIND_NODE,
    offset_s: float = 0.0,
    duration_ms: float = 1000.0,
    status: str = perf.STATUS_SUCCEEDED,
    skill_name: str | None = None,
    llm_call_count: int = 0,
    token_usage: dict[str, Any] | None = None,
    cache_hit: bool = False,
    retry_count: int = 0,
    error_type: str | None = None,
) -> NodeExecution:
    started = BASE + timedelta(seconds=offset_s)
    row = NodeExecution(
        task_id=task_id,
        ticker=ticker,
        node_name=node_name,
        skill_name=skill_name,
        span_kind=span_kind,
        started_at=started,
        completed_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        status=status,
        retry_count=retry_count,
        cache_hit=cache_hit,
        llm_call_count=llm_call_count,
        token_usage=token_usage,
        error_type=error_type,
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture(autouse=True)
def _isolate_runs():
    """Telemetry runs are process-global; never leak them between tests."""

    before = set(perf._RUNS)
    yield
    for task_id in set(perf._RUNS) - before:
        perf.drop_run(task_id)
    perf.bind_run(None)


def test_compute_parallelism_proves_overlap() -> None:
    intervals = [
        (BASE, BASE + timedelta(seconds=4), "financial_analyst"),
        (BASE, BASE + timedelta(seconds=3), "peer_analyst"),
        (BASE + timedelta(seconds=5), BASE + timedelta(seconds=6), "risk_analyst"),
    ]
    result = perf.compute_parallelism(intervals, 8000.0)
    assert result["max_concurrency"] == 2
    assert result["wall_ms"] == 6000.0
    assert result["saved_by_parallel_ms"] == 2000.0
    assert ["financial_analyst", "peer_analyst"] in result["overlap_groups"]


def test_compute_parallelism_serial_run_has_no_overlap() -> None:
    intervals = [
        (BASE, BASE + timedelta(seconds=1), "a"),
        (BASE + timedelta(seconds=1), BASE + timedelta(seconds=2), "b"),
    ]
    result = perf.compute_parallelism(intervals, 2000.0)
    assert result["max_concurrency"] == 1
    assert result["overlap_groups"] == []
    assert result["saved_by_parallel_ms"] == 0.0


def test_compute_parallelism_handles_empty_input() -> None:
    result = perf.compute_parallelism([], 0.0)
    assert result["max_concurrency"] == 0
    assert result["wall_ms"] == 0.0


def test_load_history_empty_table_returns_empty_list(session: Session) -> None:
    assert perf.load_history(session, "NVDA") == []
    summary = perf.ticker_performance(session, "NVDA")
    assert summary == {"ticker": "NVDA", "run_count": 0, "runs": [], "comparison": None}


def test_load_history_survives_broken_session() -> None:
    class BrokenSession:
        def scalars(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("no such table: node_executions")

    assert perf.load_history(BrokenSession(), "NVDA") == []
    assert perf.load_run_detail(BrokenSession(), "task") is None


def test_ticker_performance_compares_repeat_runs(session: Session) -> None:
    # Cold run: four specialists serialized -> 12s wall clock.
    for index, node in enumerate(["business_analyst", "financial_analyst", "news_collector", "peer_analyst"]):
        _add_span(
            session, "cold0001", node, offset_s=index * 3.0, duration_ms=3000.0,
            llm_call_count=1, token_usage={"total_tokens": 500},
        )
    # Warm run: cached section skipped, remaining specialists parallel -> 3.5s.
    _add_span(
        session, "warm0002", "financial_analyst", offset_s=0.0, duration_ms=2000.0,
        llm_call_count=1, token_usage={"total_tokens": 400},
    )
    _add_span(session, "warm0002", "peer_analyst", offset_s=0.0, duration_ms=3000.0, cache_hit=True)
    _add_span(
        session, "warm0002", "valuation_skill", span_kind=perf.SPAN_KIND_SKILL,
        skill_name="valuation_skill", offset_s=3.0, duration_ms=500.0,
    )

    summary = perf.ticker_performance(session, "NVDA")
    assert summary["run_count"] == 2
    assert summary["runs"][0]["task_id"] == "warm0002"  # newest first
    assert summary["runs"][-1]["task_id"] == "cold0001"

    cold, warm = summary["runs"][-1], summary["runs"][0]
    assert cold["total_duration_ms"] == pytest.approx(12000.0, abs=1.0)
    assert warm["total_duration_ms"] == pytest.approx(3500.0, abs=1.0)
    assert cold["parallelism"]["max_concurrency"] == 1
    assert warm["parallelism"]["max_concurrency"] == 2
    assert warm["cache_hits"] == 1
    assert warm["llm_call_count"] == 1
    assert warm["total_tokens"] == 400
    assert [item["skill_name"] for item in warm["skills"]] == ["valuation_skill"]

    comparison = summary["comparison"]
    assert comparison is not None
    assert comparison["saved_ms"] == pytest.approx(8500.0, abs=1.0)
    assert comparison["saved_pct"] == pytest.approx(70.8, abs=0.5)
    assert comparison["cache_hits_latest"] == 1


def test_performance_history_endpoint(client, session: Session) -> None:
    _add_span(
        session, "task0001", "financial_analyst", offset_s=0.0, duration_ms=2500.0,
        llm_call_count=2, token_usage={"total_tokens": 900},
    )
    _add_span(session, "task0001", "peer_analyst", offset_s=0.0, duration_ms=1500.0, cache_hit=True)
    _add_span(
        session, "task0001", "risk_analyst", offset_s=2.5, duration_ms=1200.0,
        status=perf.STATUS_FAILED, error_type="TimeoutError", retry_count=1,
    )

    response = client.get("/research/NVDA/performance")
    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "US.NVDA"  # endpoints report the normalized ticker
    assert data["run_count"] == 1
    run = data["runs"][0]
    assert run["task_id"] == "task0001"
    assert run["node_count"] == 3
    assert run["llm_call_count"] == 2
    assert run["total_tokens"] == 900
    assert run["cache_hits"] == 1
    assert run["retry_count"] == 1
    assert run["failed_nodes"] == ["risk_analyst"]
    assert run["error_types"] == ["TimeoutError"]
    assert run["slowest_nodes"][0]["node_name"] == "financial_analyst"
    assert data["comparison"] is None  # single run: nothing to compare yet


def test_performance_history_endpoint_limits_runs(client, session: Session) -> None:
    for index in range(5):
        _add_span(session, f"task{index:04d}", "financial_analyst", offset_s=index, duration_ms=100.0)

    response = client.get("/research/NVDA/performance", params={"limit": 3})
    assert response.status_code == 200
    assert response.json()["run_count"] == 3



def test_performance_run_detail_endpoint(client, session: Session) -> None:
    _add_span(
        session, "task0002", "business_analyst", duration_ms=1800.0, llm_call_count=1,
        token_usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )
    _add_span(
        session, "task0002", "financial_metrics_skill", span_kind=perf.SPAN_KIND_SKILL,
        skill_name="financial_metrics_skill", offset_s=1.8, duration_ms=120.0, cache_hit=True,
    )

    response = client.get("/research/NVDA/performance/task0002")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "task0002"
    assert data["ticker"] == "NVDA"
    assert len(data["spans"]) == 2
    assert {span["span_kind"] for span in data["spans"]} == {perf.SPAN_KIND_NODE, perf.SPAN_KIND_SKILL}
    assert data["cache_hits"] == 1
    assert data["total_tokens"] == 150


def test_performance_run_detail_endpoint_404(client) -> None:
    response = client.get("/research/NVDA/performance/missing-task")
    assert response.status_code == 404


def test_span_records_and_persists_a_full_run(session: Session) -> None:
    """Round-trip: live spans -> ``finish_run`` -> ``node_executions`` -> history."""

    run = perf.start_run("NVDA")
    with perf.span("financial_analyst") as record:
        perf.record_llm_call(model="qwen-plus", duration_ms=12.0, prompt_tokens=10,
                             completion_tokens=5, total_tokens=15)
        record.cache_hit = False
    with perf.span(
        "financial_metrics_skill", span_kind=perf.SPAN_KIND_SKILL, skill_name="financial_metrics_skill"
    ) as skill_record:
        skill_record.cache_hit = True
        perf.record_cache_event(True)
    summary = perf.finish_run(run.task_id, persist_session=session)
    session.commit()

    assert summary["llm_call_count"] == 1
    assert summary["token_usage"]["total_tokens"] == 15
    assert summary["cache_hit_rate"] == 1.0

    history = perf.load_history(session, "NVDA")
    assert len(history) == 1
    assert history[0]["task_id"] == run.task_id
    assert history[0]["llm_call_count"] == 1
    assert history[0]["total_tokens"] == 15
    assert history[0]["cache_hits"] >= 1
    assert {item["skill_name"] for item in history[0]["skills"]} == {"financial_metrics_skill"}

    detail = perf.load_run_detail(session, run.task_id)
    assert detail is not None
    assert {span["span_kind"] for span in detail["spans"]} >= {perf.SPAN_KIND_NODE, perf.SPAN_KIND_SKILL}


def test_spans_without_active_run_are_noop() -> None:
    """Telemetry must never break the workflow when tracking is disabled."""

    with perf.span("financial_analyst") as record:
        assert record.task_id == ""
    perf.record_llm_call(total_tokens=5)
    perf.record_cache_event(True)
    perf.record_retry()
    assert perf.finish_run("does-not-exist") == {}

