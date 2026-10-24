"""Phase 2 regression tests: real parallel execution of the workflow DAG.

These use fake sleeping nodes (no network, no LLM) so they prove *scheduling*
behaviour deterministically: that independent specialists genuinely overlap,
that declared dependencies are still honoured, and that shared state is merged
safely.
"""

from __future__ import annotations

import collections
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.graph.state import create_initial_state
from app.services.research_runner import (
    PARALLEL_SPECIALISTS,
    REDUCER_FIELDS,
    WORKFLOW_DAG,
    WORKFLOW_STEPS,
    ResearchRunner,
    merge_state,
)

TICKER = "US.NVDA"
NAME = "NVIDIA"


@pytest.fixture()
def file_session(tmp_path):
    """File-backed SQLite so each worker thread gets its own connection.

    The in-memory ``session`` fixture shares one connection via StaticPool,
    which cannot be used from several threads; the runner therefore refuses to
    parallelize on it (see ``ResearchRunner._default_session_factory``).
    """

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'parallel.db'}",
        future=True,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db = factory()
    try:
        yield db, factory
    finally:
        db.close()
        engine.dispose()


class FakeNode:
    """Stand-in workflow node that records when it ran and sleeps."""

    timeline: list[tuple[str, float, float]] = []
    lock = threading.Lock()

    def __init__(self, step_id: str, delay: float) -> None:
        self.step_id = step_id
        self.delay = delay
        self.session_id: int | None = None

    def __call__(self, state: dict) -> dict:
        start = time.perf_counter()
        time.sleep(self.delay)
        end = time.perf_counter()
        with FakeNode.lock:
            FakeNode.timeline.append((self.step_id, start, end))
        return {"errors": [f"{self.step_id}-error"]}


@pytest.fixture(autouse=True)
def _reset_timeline():
    FakeNode.timeline = []
    yield
    FakeNode.timeline = []


def make_runner(db: Session, factory, delays: dict[str, float], parallel: bool = True) -> ResearchRunner:
    """Build a runner whose nodes are all fakes with the given delays."""

    runner = ResearchRunner(
        db,
        model=None,
        session_factory=factory if parallel else None,
        max_workers=4 if parallel else 1,
    )

    def fake_build_node(step_id: str, node_cls: type, session: Session | None = None):
        node = FakeNode(step_id, delays.get(step_id, 0.0))
        node.session_id = id(session)
        return node

    runner._build_node = fake_build_node  # type: ignore[method-assign]
    runner._persist_news_and_events = lambda state: None  # type: ignore[method-assign]
    runner._commit_checkpoint = lambda step_id: None  # type: ignore[method-assign]
    runner._build_step_details = lambda step_id, node, state: {}  # type: ignore[method-assign]
    runner._persist_performance = lambda task_id: None  # type: ignore[method-assign]
    return runner


def intervals(step_id: str) -> tuple[float, float]:
    for name, start, end in FakeNode.timeline:
        if name == step_id:
            return start, end
    raise AssertionError(f"{step_id} never ran")


# ------------------------------------------------------------------- DAG shape


def test_workflow_dag_is_acyclic_and_deps_resolve() -> None:
    ids = {spec.step_id for spec in WORKFLOW_DAG}
    for spec in WORKFLOW_DAG:
        assert set(spec.depends_on) <= ids, f"{spec.step_id} depends on unknown node"

    indegree = {spec.step_id: len(spec.depends_on) for spec in WORKFLOW_DAG}
    dependents: dict[str, list[str]] = collections.defaultdict(list)
    for spec in WORKFLOW_DAG:
        for dep in spec.depends_on:
            dependents[dep].append(spec.step_id)

    queue = [k for k, v in indegree.items() if v == 0]
    visited: list[str] = []
    while queue:
        node = queue.pop(0)
        visited.append(node)
        for child in dependents[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    assert len(visited) == len(WORKFLOW_DAG), f"cycle detected, only ordered {visited}"


def test_workflow_steps_still_exposes_every_node_in_topological_order() -> None:
    """Backward compatibility: existing callers import WORKFLOW_STEPS."""

    assert [step[0] for step in WORKFLOW_STEPS] == [spec.step_id for spec in WORKFLOW_DAG]
    assert len(WORKFLOW_STEPS) == 11
    for step_id, label, node_cls in WORKFLOW_STEPS:
        assert isinstance(label, str) and label
        assert isinstance(node_cls, type)


def test_valuation_no_longer_waits_for_business_news_or_peer_branches() -> None:
    """The measured critical-path improvement: valuation depends only on financials."""

    spec = next(s for s in WORKFLOW_DAG if s.step_id == "valuation_agent")
    assert spec.depends_on == ("financial_analyst",)


def test_peer_analyst_keeps_its_real_dependency_on_business_analysis() -> None:
    spec = next(s for s in WORKFLOW_DAG if s.step_id == "peer_analyst")
    assert "business_analyst" in spec.depends_on


def test_all_four_specialists_are_parallel_candidates() -> None:
    assert PARALLEL_SPECIALISTS == {"business_analyst", "news_analyst", "financial_analyst", "peer_analyst"}


# --------------------------------------------------------------- state merging


def test_reducer_fields_detected_from_state_annotations() -> None:
    assert REDUCER_FIELDS == {"errors", "warnings"}


def test_merge_state_concatenates_reducer_lists_instead_of_overwriting() -> None:
    state = {"errors": ["a"], "warnings": []}
    merge_state(state, {"errors": ["b"], "financial_summary": {"x": 1}})
    merge_state(state, {"errors": ["c"], "warnings": ["w"]})
    assert state["errors"] == ["a", "b", "c"]
    assert state["warnings"] == ["w"]
    assert state["financial_summary"] == {"x": 1}


def test_merge_state_overwrites_non_reducer_fields() -> None:
    state = {"financial_summary": {"v": 1}}
    merge_state(state, {"financial_summary": {"v": 2}})
    assert state["financial_summary"] == {"v": 2}



# ---------------------------------------------------------- parallel execution


def test_independent_specialists_actually_overlap(file_session) -> None:
    """Direct evidence of parallelism: measured concurrency > 1."""

    db, factory = file_session
    delays = {
        "research_planner": 0.01,
        "data_availability_checker": 0.01,
        "data_fetcher": 0.01,
        "company_profiler": 0.01,
        "business_analyst": 0.30,
        "news_analyst": 0.30,
        "financial_analyst": 0.30,
        "peer_analyst": 0.10,
        "valuation_agent": 0.05,
        "risk_analyst": 0.05,
        "synthesis_reviewer": 0.05,
    }
    runner = make_runner(db, factory, delays)
    state = create_initial_state(TICKER, NAME)
    summary = runner._execute_dag(state, lambda sid, status, payload: None)

    parallelism = summary["parallelism"]
    assert parallelism["max_concurrency"] >= 3, f"expected overlap, got {parallelism}"
    # Three 0.30 s specialists in parallel must beat their 0.90 s serial sum.
    assert parallelism["saved_by_parallel_ms"] > 300, parallelism
    assert parallelism["wall_ms"] < parallelism["serial_sum_ms"]

    groups = parallelism["overlap_groups"]
    assert any(
        {"business_analyst", "news_analyst", "financial_analyst"} <= set(group) for group in groups
    ), groups


def test_declared_dependencies_are_still_honoured(file_session) -> None:
    """peer_analyst must not start before business_analyst finished."""

    db, factory = file_session
    delays = {spec.step_id: 0.02 for spec in WORKFLOW_DAG}
    delays["business_analyst"] = 0.25
    runner = make_runner(db, factory, delays)
    state = create_initial_state(TICKER, NAME)
    runner._execute_dag(state, lambda sid, status, payload: None)

    _business_start, business_end = intervals("business_analyst")
    peer_start, peer_end = intervals("peer_analyst")
    assert peer_start >= business_end, "peer_analyst started before business_analyst finished"

    _financial_start, financial_end = intervals("financial_analyst")
    valuation_start, _ = intervals("valuation_agent")
    assert valuation_start >= financial_end

    risk_start, _ = intervals("risk_analyst")
    assert risk_start >= intervals("valuation_agent")[1]
    assert risk_start >= intervals("news_analyst")[1]
    assert risk_start >= peer_end

    synthesis_start, _ = intervals("synthesis_reviewer")
    assert synthesis_start >= intervals("risk_analyst")[1]
    # valuation must not have been held back by the slow business branch
    assert valuation_start < business_end



def test_every_node_runs_exactly_once(file_session) -> None:
    db, factory = file_session
    runner = make_runner(db, factory, {spec.step_id: 0.0 for spec in WORKFLOW_DAG})
    state = create_initial_state(TICKER, NAME)
    runner._execute_dag(state, lambda sid, status, payload: None)

    executed = [name for name, _, _ in FakeNode.timeline]
    assert sorted(executed) == sorted(spec.step_id for spec in WORKFLOW_DAG)


def test_parallel_workers_each_get_their_own_session(file_session) -> None:
    """§5.3: no two parallel nodes may share one SQLAlchemy Session."""

    db, factory = file_session
    delays = {spec.step_id: 0.0 for spec in WORKFLOW_DAG}
    for specialist in PARALLEL_SPECIALISTS:
        delays[specialist] = 0.15
    runner = make_runner(db, factory, delays)

    sessions_by_step: dict[str, int] = {}
    original = runner._build_node

    def tracking_build(step_id, node_cls, session=None):
        node = original(step_id, node_cls, session=session)
        sessions_by_step[step_id] = node.session_id
        return node

    runner._build_node = tracking_build  # type: ignore[method-assign]
    state = create_initial_state(TICKER, NAME)
    runner._execute_dag(state, lambda sid, status, payload: None)

    specialist_sessions = {sid: sessions_by_step[sid] for sid in PARALLEL_SPECIALISTS}
    assert len(set(specialist_sessions.values())) == len(PARALLEL_SPECIALISTS), specialist_sessions
    # coordinating-thread nodes keep using the runner's own session
    assert sessions_by_step["research_planner"] == id(db)


def test_progress_events_emitted_for_every_node(file_session) -> None:
    db, factory = file_session
    runner = make_runner(db, factory, {spec.step_id: 0.0 for spec in WORKFLOW_DAG})
    state = create_initial_state(TICKER, NAME)
    events: list[tuple[str, str]] = []
    runner._execute_dag(state, lambda sid, status, payload: events.append((sid, status)))

    started = [sid for sid, status in events if status == "started"]
    completed = [sid for sid, status in events if status == "completed"]
    assert sorted(started) == sorted(spec.step_id for spec in WORKFLOW_DAG)
    assert sorted(completed) == sorted(spec.step_id for spec in WORKFLOW_DAG)
    for sid in started:
        assert events.index((sid, "started")) < events.index((sid, "completed"))


def test_errors_from_parallel_branches_are_all_preserved(file_session) -> None:
    db, factory = file_session
    runner = make_runner(db, factory, {spec.step_id: 0.0 for spec in WORKFLOW_DAG})
    state = create_initial_state(TICKER, NAME)
    runner._execute_dag(state, lambda sid, status, payload: None)

    errors = state.get("errors", [])
    for spec in WORKFLOW_DAG:
        assert f"{spec.step_id}-error" in errors, f"lost error from {spec.step_id}"


def test_sequential_fallback_when_bind_is_not_thread_safe() -> None:
    """In-memory SQLite (the default test fixture) must not be parallelized."""

    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    try:
        runner = ResearchRunner(db, model=None)
        assert runner._session_factory is None
        assert runner.parallel_enabled is False
    finally:
        db.close()
        engine.dispose()


def test_explicit_session_factory_enables_parallelism(file_session) -> None:
    db, factory = file_session
    runner = ResearchRunner(db, model=None, session_factory=factory)
    assert runner.parallel_enabled is True

