"""Lightweight execution telemetry for research runs.

Design goals (optimization task §10):
  * Answer, with real measured numbers: total runtime, slowest nodes, whether
    specialists actually overlapped, download vs parse cost, cache hit rate,
    LLM call count and token usage, which errors triggered retries, and how
    much time a repeat run saved.
  * Zero new dependencies — stdlib only, plus the existing structlog logger and
    the existing SQLAlchemy session for optional persistence.
  * Never break the workflow: every public helper degrades to a no-op when no
    run is active or when the ``node_executions`` table is missing.

Spans are attributed to the *innermost* active span of the current thread via a
``ContextVar`` stack, so a skill called from inside a node is nested under that
node. Worker threads must propagate the run id explicitly — use
:func:`thread_context` when submitting work to an executor.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any
import uuid

from app.core.logging import get_logger

logger = get_logger(__name__)

SPAN_KIND_NODE = "node"
SPAN_KIND_SKILL = "skill"
SPAN_KIND_LLM = "llm"
SPAN_KIND_IO = "io"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_RETRYING = "retrying"
STATUS_SKIPPED = "skipped"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_triple(span: Any) -> tuple[int, int, int]:
    """Read prompt/completion/total tokens from a span record *or* an ORM row."""

    usage = getattr(span, "token_usage", None)
    if isinstance(usage, dict) and usage:
        return (
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("total_tokens") or 0),
        )
    return (
        int(getattr(span, "prompt_tokens", 0) or 0),
        int(getattr(span, "completion_tokens", 0) or 0),
        int(getattr(span, "total_tokens", 0) or 0),
    )


def _count_llm_calls(spans: list[Any]) -> int:
    """Count LLM round-trips without double counting.

    ``record_llm_call`` attributes a call to the owning span *and* appends a
    dedicated ``llm`` span, so summing every span would report each call twice.
    """

    llm_spans = [s for s in spans if getattr(s, "span_kind", None) == SPAN_KIND_LLM]
    return sum(int(getattr(s, "llm_call_count", 0) or 0) for s in (llm_spans or spans))


def _sum_token_usage(spans: list[Any]) -> dict[str, int]:
    """Sum tokens once per call, mirroring :func:`_count_llm_calls`."""

    llm_spans = [s for s in spans if getattr(s, "span_kind", None) == SPAN_KIND_LLM]
    prompt = completion = total = 0
    for span in llm_spans or spans:
        p, c, t = _token_triple(span)
        prompt += p
        completion += c
        total += t
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def ticker_variants(ticker: str) -> list[str]:
    """All stored forms of *ticker* (``US.NVDA`` and ``NVDA``).

    Telemetry is written with whatever ticker the run used, so history lookups
    accept both the normalized and the bare symbol form.
    """

    from app.utils.metadata_utils import normalize_ticker

    raw = (ticker or "").strip().upper()
    if not raw:
        return []
    candidates = [raw, normalize_ticker(raw)]
    if "." in raw:
        candidates.append(raw.split(".", 1)[1])
    variants: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def compute_parallelism(
    intervals: list[tuple[datetime, datetime, str]], serial_sum_ms: float
) -> dict[str, Any]:
    """Derive overlap statistics from ``(started_at, completed_at, name)`` tuples.

    Shared by the in-memory :class:`RunMetrics` view and by history loaded from
    ``node_executions`` so both report parallelism the same way. ``intervals``
    must be homogeneous w.r.t. timezone awareness (all naive or all aware).
    """

    usable = [(start, end, name) for start, end, name in intervals if start and end]
    if not usable:
        return {
            "max_concurrency": 0,
            "serial_sum_ms": round(serial_sum_ms, 1),
            "wall_ms": 0.0,
            "saved_by_parallel_ms": 0.0,
            "overlap_groups": [],
        }

    events: list[tuple[datetime, int]] = []
    for start, end, _ in usable:
        events.append((start, 1))
        events.append((end, -1))
    # Sort so an ending span is processed before a starting one at the same
    # instant; this avoids inflating concurrency on back-to-back nodes.
    events.sort(key=lambda e: (e[0], e[1]))
    depth = max_depth = 0
    for _, delta in events:
        depth += delta
        max_depth = max(max_depth, depth)

    wall = (max(end for _, end, _ in usable) - min(start for start, _, _ in usable)).total_seconds() * 1000

    # Group nodes that were in flight together — the concrete parallel sets.
    overlap_groups: list[list[str]] = []
    for start, end, name in usable:
        peers = sorted(
            other for o_start, o_end, other in usable if other != name and o_start < end and start < o_end
        )
        if peers:
            group = sorted([name, *peers])
            if group not in overlap_groups:
                overlap_groups.append(group)

    return {
        "max_concurrency": max_depth,
        "serial_sum_ms": round(serial_sum_ms, 1),
        "wall_ms": round(wall, 1),
        "saved_by_parallel_ms": round(max(serial_sum_ms - wall, 0.0), 1),
        "overlap_groups": overlap_groups,
    }


@dataclass
class SpanRecord:
    """One measured execution span."""

    task_id: str
    node_name: str
    started_at: datetime
    span_kind: str = SPAN_KIND_NODE
    skill_name: str | None = None
    ticker: str | None = None
    company_id: int | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    status: str = STATUS_RUNNING
    retry_count: int = 0
    cache_hit: bool = False
    llm_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error_type: str | None = None
    detail: str | None = None
    thread_id: int = field(default_factory=lambda: threading.get_ident())

    @property
    def token_usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def as_row(self) -> dict[str, Any]:
        """Flatten to the ``node_executions`` column set."""

        return {
            "task_id": self.task_id,
            "company_id": self.company_id,
            "ticker": self.ticker,
            "node_name": self.node_name,
            "skill_name": self.skill_name,
            "span_kind": self.span_kind,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "retry_count": self.retry_count,
            "cache_hit": self.cache_hit,
            "llm_call_count": self.llm_call_count,
            "token_usage": self.token_usage,
            "error_type": self.error_type,
            "detail": self.detail,
        }


@dataclass
class RunMetrics:
    """Accumulated telemetry for a single research run."""

    task_id: str
    ticker: str = ""
    company_id: int | None = None
    started_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None
    spans: list[SpanRecord] = field(default_factory=list)
    cache_lookups: int = 0
    cache_hits: int = 0
    retries: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------ record

    def add_span(self, record: SpanRecord) -> None:
        with self._lock:
            self.spans.append(record)

    def record_cache(self, hit: bool) -> None:
        with self._lock:
            self.cache_lookups += 1
            if hit:
                self.cache_hits += 1

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1

    @property
    def llm_call_count(self) -> int:
        """Total LLM round-trips in this run.

        ``record_llm_call`` attributes a call both to the owning span and to a
        dedicated ``llm`` span, so counting every span would double-report.
        Prefer the dedicated spans; fall back to owner attribution for callers
        that only set ``llm_call_count`` on their own span.
        """

        return _count_llm_calls(self.spans)

    @property
    def token_usage(self) -> dict[str, int]:
        return _sum_token_usage(self.spans)

    @property
    def cache_hit_rate(self) -> float | None:
        return (self.cache_hits / self.cache_lookups) if self.cache_lookups else None

    def duration_ms(self) -> float | None:
        end = self.completed_at or _utcnow()
        return (end - self.started_at).total_seconds() * 1000


    # ----------------------------------------------------------------- reports

    def node_timings(self) -> list[dict[str, Any]]:
        """Per-node timings sorted slowest first (§10.1 question 2)."""

        rows = [s for s in self.spans if s.span_kind == SPAN_KIND_NODE and s.duration_ms is not None]
        return [
            {
                "node_name": s.node_name,
                "duration_ms": round(s.duration_ms, 1),
                "status": s.status,
                "cache_hit": s.cache_hit,
                "llm_call_count": s.llm_call_count,
                "started_at": s.started_at.isoformat(),
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            }
            for s in sorted(rows, key=lambda r: -r.duration_ms)
        ]

    def skill_timings(self) -> list[dict[str, Any]]:
        """Per-skill timings, separating download from parse cost (question 4)."""

        rows = [s for s in self.spans if s.span_kind in {SPAN_KIND_SKILL, SPAN_KIND_IO} and s.duration_ms is not None]
        return [
            {
                "skill_name": s.skill_name or s.node_name,
                "span_kind": s.span_kind,
                "duration_ms": round(s.duration_ms, 1),
                "status": s.status,
                "cache_hit": s.cache_hit,
            }
            for s in sorted(rows, key=lambda r: -r.duration_ms)
        ]

    def parallelism(self) -> dict[str, Any]:
        """Prove whether spans really overlapped (question 3).

        ``max_concurrency`` is sampled from interval endpoints, so a value > 1
        is direct evidence of genuine parallel execution rather than an
        assumption drawn from the graph shape.
        """

        intervals = [
            (s.started_at, s.completed_at, s.node_name)
            for s in self.spans
            if s.span_kind == SPAN_KIND_NODE and s.completed_at and s.duration_ms
        ]
        serial_sum = sum(s.duration_ms for s in self.spans if s.span_kind == SPAN_KIND_NODE and s.duration_ms)
        return compute_parallelism(intervals, serial_sum)

    def failures(self) -> list[dict[str, Any]]:
        """Errors that caused retries or hard failures (question 7)."""

        return [
            {
                "node_name": s.node_name,
                "skill_name": s.skill_name,
                "status": s.status,
                "error_type": s.error_type,
                "retry_count": s.retry_count,
                "detail": (s.detail or "")[:300],
            }
            for s in self.spans
            if s.status in {STATUS_FAILED, STATUS_RETRYING} or s.error_type
        ]

    def summary(self) -> dict[str, Any]:
        """Answer every question listed in optimization task §10.1."""

        nodes = [s for s in self.spans if s.span_kind == SPAN_KIND_NODE]
        rate = self.cache_hit_rate
        return {
            "task_id": self.task_id,
            "ticker": self.ticker,
            "company_id": self.company_id,
            "total_duration_ms": round(self.duration_ms() or 0.0, 1),
            "node_count": len(nodes),
            "failed_nodes": [s.node_name for s in nodes if s.status == STATUS_FAILED],
            "skipped_nodes": [s.node_name for s in nodes if s.status == STATUS_SKIPPED],
            "slowest_nodes": self.node_timings()[:5],
            "skills": self.skill_timings()[:10],
            "parallelism": self.parallelism(),
            "llm_call_count": self.llm_call_count,
            "token_usage": self.token_usage,
            "cache_lookups": self.cache_lookups,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": round(rate, 4) if rate is not None else None,
            "retry_count": self.retries,
            "failures": self.failures(),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }



# ---------------------------------------------------------------- module state

_RUNS: dict[str, RunMetrics] = {}
_RUNS_LOCK = threading.Lock()
_current_task: ContextVar[str | None] = ContextVar("perf_current_task", default=None)
_span_stack: ContextVar[tuple[SpanRecord, ...]] = ContextVar("perf_span_stack", default=())

MAX_TRACKED_RUNS = 64


def new_task_id() -> str:
    return uuid.uuid4().hex[:16]


def start_run(ticker: str = "", company_id: int | None = None, task_id: str | None = None) -> RunMetrics:
    """Register a new run and bind it to the calling thread/context."""

    metrics = RunMetrics(task_id=task_id or new_task_id(), ticker=ticker, company_id=company_id)
    with _RUNS_LOCK:
        _RUNS[metrics.task_id] = metrics
        # Bound the registry so a long-lived server process does not leak runs.
        if len(_RUNS) > MAX_TRACKED_RUNS:
            for stale in sorted(_RUNS, key=lambda k: _RUNS[k].started_at)[: len(_RUNS) - MAX_TRACKED_RUNS]:
                _RUNS.pop(stale, None)
    bind_run(metrics.task_id)
    logger.info("perf_run_started", task_id=metrics.task_id, ticker=ticker)
    return metrics


def bind_run(task_id: str | None) -> None:
    """Bind *task_id* to the current execution context (thread-safe)."""

    _current_task.set(task_id)


def current_run_id() -> str | None:
    return _current_task.get()


def get_run(task_id: str | None = None) -> RunMetrics | None:
    key = task_id or _current_task.get()
    if not key:
        return None
    with _RUNS_LOCK:
        return _RUNS.get(key)


def drop_run(task_id: str) -> None:
    with _RUNS_LOCK:
        _RUNS.pop(task_id, None)


@contextmanager
def span(
    node_name: str,
    *,
    span_kind: str = SPAN_KIND_NODE,
    skill_name: str | None = None,
    task_id: str | None = None,
) -> Iterator[SpanRecord]:
    """Measure a block of work and attribute it to the active run.

    Yields a mutable :class:`SpanRecord` so callers can flag ``cache_hit``,
    ``retry_count`` or ``status`` from inside the block. With no active run the
    block still executes — telemetry is strictly optional.
    """

    run = get_run(task_id)
    record = SpanRecord(
        task_id=run.task_id if run else "",
        node_name=node_name,
        span_kind=span_kind,
        skill_name=skill_name,
        started_at=_utcnow(),
        ticker=run.ticker if run else None,
        company_id=run.company_id if run else None,
    )
    if run is None:
        # No active run: execute the body untracked.
        yield record
        return

    token = _span_stack.set((*_span_stack.get(), record))
    t0 = time.perf_counter()
    try:
        yield record
        if record.status == STATUS_RUNNING:
            record.status = STATUS_SUCCEEDED
    except BaseException as exc:  # noqa: BLE001 - record then re-raise untouched
        record.status = STATUS_FAILED
        record.error_type = type(exc).__name__
        record.detail = str(exc)[:500]
        raise
    finally:
        _span_stack.reset(token)
        record.duration_ms = (time.perf_counter() - t0) * 1000
        record.completed_at = _utcnow()
        run.add_span(record)
        logger.info(
            "perf_span_completed",
            task_id=run.task_id,
            span=node_name,
            kind=span_kind,
            skill=skill_name,
            duration_ms=round(record.duration_ms, 1),
            status=record.status,
            cache_hit=record.cache_hit,
        )


def parent_span() -> SpanRecord | None:
    """Return the innermost active span, if any."""

    stack = _span_stack.get()
    return stack[-1] if stack else None



def record_llm_call(
    *,
    model: str | None = None,
    duration_ms: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    status: str = STATUS_SUCCEEDED,
    error_type: str | None = None,
) -> None:
    """Attribute one LLM round-trip to the innermost span and the run total."""

    run = get_run()
    owner = parent_span()
    if owner is None and run is not None and run.spans:
        owner = run.spans[-1]
    tokens = total_tokens or (prompt_tokens + completion_tokens)
    if owner is not None:
        owner.llm_call_count += 1
        owner.prompt_tokens += prompt_tokens
        owner.completion_tokens += completion_tokens
        owner.total_tokens += tokens
    if run is not None:
        now = _utcnow()
        run.add_span(
            SpanRecord(
                task_id=run.task_id,
                node_name=owner.node_name if owner else "llm",
                span_kind=SPAN_KIND_LLM,
                skill_name=model,
                started_at=now,
                completed_at=now,
                duration_ms=duration_ms,
                status=status,
                llm_call_count=1,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=tokens,
                error_type=error_type,
                ticker=run.ticker,
                company_id=run.company_id,
            )
        )


def record_cache_event(hit: bool) -> None:
    """Count one cache lookup; a hit also flags the owning span."""

    run = get_run()
    if run is None:
        return
    run.record_cache(hit)
    owner = parent_span()
    if owner is not None and hit:
        owner.cache_hit = True


def record_retry() -> None:
    run = get_run()
    if run is None:
        return
    run.record_retry()
    owner = parent_span()
    if owner is not None:
        owner.retry_count += 1


def mark_skipped(node_name: str, reason: str = "") -> None:
    """Record a zero-cost span for work skipped via cache reuse."""

    run = get_run()
    if run is None:
        return
    now = _utcnow()
    run.add_span(
        SpanRecord(
            task_id=run.task_id,
            node_name=node_name,
            span_kind=SPAN_KIND_NODE,
            started_at=now,
            completed_at=now,
            duration_ms=0.0,
            status=STATUS_SKIPPED,
            cache_hit=True,
            detail=reason or None,
            ticker=run.ticker,
            company_id=run.company_id,
        )
    )



def finish_run(task_id: str | None = None, *, persist_session: Any | None = None) -> dict[str, Any]:
    """Close the run, optionally persist spans, and return the summary dict."""

    run = get_run(task_id)
    if run is None:
        return {}
    run.completed_at = _utcnow()
    summary = run.summary()
    logger.info(
        "perf_run_completed",
        task_id=run.task_id,
        ticker=run.ticker,
        total_duration_ms=summary["total_duration_ms"],
        llm_calls=summary["llm_call_count"],
        total_tokens=summary["token_usage"]["total_tokens"],
        cache_hit_rate=summary["cache_hit_rate"],
        max_concurrency=summary["parallelism"]["max_concurrency"],
        saved_by_parallel_ms=summary["parallelism"].get("saved_by_parallel_ms"),
        failed=summary["failed_nodes"],
    )
    if persist_session is not None:
        persist_run(persist_session, run)
    return summary


def persist_run(session: Any, run: RunMetrics) -> int:
    """Write spans to ``node_executions``. Never raises into the workflow."""

    try:
        from app.db.models import NodeExecution

        for record in run.spans:
            session.add(NodeExecution(**record.as_row()))
        session.flush()
        return len(run.spans)
    except Exception as exc:  # noqa: BLE001
        logger.warning("perf_persist_failed", task_id=run.task_id, error=str(exc)[:200])
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


def thread_context(fn: Callable[..., Any], task_id: str | None) -> Callable[..., Any]:
    """Wrap *fn* so a worker thread reports telemetry into the same run.

    ``ThreadPoolExecutor`` does not propagate ``ContextVar`` values, so without
    this wrapper every span recorded inside a parallel branch would be dropped.
    """

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        bind_run(task_id)
        return fn(*args, **kwargs)

    return wrapper


def load_history(session: Any, ticker: str, limit: int = 20) -> list[dict[str, Any]]:
    """Load past run summaries so repeat-run savings are measurable (question 8).

    Two queries in total (recent task ids, then all their spans) so the endpoint
    stays cheap even when ``node_executions`` grows.
    """

    from sqlalchemy import select

    from app.db.models import NodeExecution

    try:
        task_ids = list(
            session.scalars(
                select(NodeExecution.task_id)
                .where(NodeExecution.ticker.in_(ticker_variants(ticker)), NodeExecution.span_kind == SPAN_KIND_NODE)
                .distinct()
                .order_by(NodeExecution.task_id.desc())
                .limit(limit)
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("perf_history_unavailable", ticker=ticker, error=str(exc)[:200])
        return []
    if not task_ids:
        return []

    try:
        rows = list(session.scalars(select(NodeExecution).where(NodeExecution.task_id.in_(task_ids))))
    except Exception as exc:  # noqa: BLE001
        logger.warning("perf_history_spans_unavailable", ticker=ticker, error=str(exc)[:200])
        return []

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.task_id, []).append(row)

    history: list[dict[str, Any]] = []
    for task_id in task_ids:
        summary = summarize_spans(task_id, ticker, grouped.get(task_id) or [])
        if summary is not None:
            history.append(summary)
    # Newest first — callers diff consecutive runs to quantify repeat savings.
    history.sort(key=lambda item: item.get("started_at") or "", reverse=True)
    return history


def summarize_spans(task_id: str, ticker: str, spans: list[Any]) -> dict[str, Any] | None:
    """Turn persisted ``node_executions`` rows into one run summary dict."""

    nodes = [s for s in spans if s.span_kind == SPAN_KIND_NODE]
    if not nodes:
        return None

    started = [s.started_at for s in spans if s.started_at]
    completed = [s.completed_at for s in spans if s.completed_at]
    serial_sum = sum(s.duration_ms or 0.0 for s in nodes)
    intervals = [(s.started_at, s.completed_at, s.node_name) for s in nodes if s.started_at and s.completed_at]
    parallel = compute_parallelism(intervals, serial_sum)
    # Wall clock spans every span kind: a run is not over when the last *node*
    # closes (e.g. a valuation skill still running after the analysts).
    wall_ms = parallel["wall_ms"]
    if started and completed:
        wall_ms = round((max(completed) - min(started)).total_seconds() * 1000, 1)

    node_timings = sorted(
        (
            {
                "node_name": s.node_name,
                "duration_ms": round(s.duration_ms or 0.0, 1),
                "status": s.status,
                "cache_hit": bool(s.cache_hit),
                "llm_call_count": s.llm_call_count or 0,
            }
            for s in nodes
            if s.duration_ms is not None
        ),
        key=lambda item: -item["duration_ms"],
    )
    skill_timings = sorted(
        (
            {
                "skill_name": s.skill_name or s.node_name,
                "span_kind": s.span_kind,
                "duration_ms": round(s.duration_ms or 0.0, 1),
                "status": s.status,
                "cache_hit": bool(s.cache_hit),
            }
            for s in spans
            if s.span_kind in {SPAN_KIND_SKILL, SPAN_KIND_IO} and s.duration_ms is not None
        ),
        key=lambda item: -item["duration_ms"],
    )

    return {
        "task_id": task_id,
        "ticker": ticker,
        "started_at": min(started).isoformat() if started else None,
        "completed_at": max(completed).isoformat() if completed else None,
        # Wall clock across every span kind; falls back to the node sum when a
        # run was interrupted before spans closed.
        "total_duration_ms": wall_ms or (round(serial_sum, 1) if serial_sum else None),
        "node_count": len(nodes),
        "serial_sum_ms": parallel["serial_sum_ms"],
        "parallelism": {
            "max_concurrency": parallel["max_concurrency"],
            "saved_by_parallel_ms": parallel["saved_by_parallel_ms"],
            "overlap_groups": parallel["overlap_groups"][:8],
        },
        "slowest_nodes": node_timings[:5],
        "skills": skill_timings[:10],
        "llm_call_count": _count_llm_calls(spans),
        "total_tokens": _sum_token_usage(spans)["total_tokens"],
        "cache_hits": sum(1 for s in spans if s.cache_hit),
        "retry_count": sum(s.retry_count or 0 for s in spans),
        "failed_nodes": [s.node_name for s in nodes if s.status == STATUS_FAILED],
        "skipped_nodes": [s.node_name for s in nodes if s.status == STATUS_SKIPPED],
        "error_types": sorted({s.error_type for s in spans if s.error_type}),
    }




def ticker_performance(session: Any, ticker: str, limit: int = 10) -> dict[str, Any]:
    """Aggregate persisted telemetry for one ticker (optimization task §10.1).

    Returns the per-run history plus an explicit repeat-run comparison so the
    "how much time did the second run save" question is answered with measured
    numbers rather than an estimate.
    """

    runs = load_history(session, ticker, limit=limit)
    comparison: dict[str, Any] | None = None
    if len(runs) >= 2:
        # ``runs`` is newest-first, so the oldest loaded run is the baseline.
        baseline, latest = runs[-1], runs[0]
        base_ms = float(baseline.get("total_duration_ms") or 0.0)
        latest_ms = float(latest.get("total_duration_ms") or 0.0)
        comparison = {
            "baseline_task_id": baseline["task_id"],
            "latest_task_id": latest["task_id"],
            "baseline_duration_ms": base_ms,
            "latest_duration_ms": latest_ms,
            "saved_ms": round(base_ms - latest_ms, 1),
            "saved_pct": round((base_ms - latest_ms) / base_ms * 100, 1) if base_ms else None,
            "llm_calls_baseline": baseline.get("llm_call_count", 0),
            "llm_calls_latest": latest.get("llm_call_count", 0),
            "cache_hits_baseline": baseline.get("cache_hits", 0),
            "cache_hits_latest": latest.get("cache_hits", 0),
        }
    return {"ticker": ticker, "run_count": len(runs), "runs": runs, "comparison": comparison}


def load_run_detail(session: Any, task_id: str) -> dict[str, Any] | None:
    """Full per-span detail for one run (every node, skill and LLM span)."""

    from sqlalchemy import select

    from app.db.models import NodeExecution

    try:
        rows = list(
            session.scalars(
                select(NodeExecution).where(NodeExecution.task_id == task_id).order_by(NodeExecution.started_at)
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("perf_detail_unavailable", task_id=task_id, error=str(exc)[:200])
        return None
    if not rows:
        return None

    ticker = next((r.ticker for r in rows if r.ticker), "") or ""
    summary = summarize_spans(task_id, ticker, rows)
    spans = [
        {
            "node_name": r.node_name,
            "skill_name": r.skill_name,
            "span_kind": r.span_kind,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "duration_ms": round(r.duration_ms, 1) if r.duration_ms is not None else None,
            "status": r.status,
            "retry_count": r.retry_count or 0,
            "cache_hit": bool(r.cache_hit),
            "llm_call_count": r.llm_call_count or 0,
            "token_usage": r.token_usage,
            "error_type": r.error_type,
            "detail": (r.detail or "")[:300] if r.detail else None,
        }
        for r in rows
    ]
    return {**(summary or {"task_id": task_id, "ticker": ticker}), "spans": spans}

