"""Research workflow execution and persistence helpers — V3.

Execution model (optimization task §5)
--------------------------------------
``WORKFLOW_STEPS`` used to be walked strictly one node at a time, so the four
specialists ran serially even though ``app/graph/workflow.py`` describes them as
a parallel fan-out (that graph was only ever exercised by tests).

The runner now executes ``WORKFLOW_DAG``, whose edges are derived from the state
fields each node actually reads::

    research_planner
      -> data_availability_checker
        -> data_fetcher
          -> company_profiler
            -> business_analyst -> peer_analyst
            -> news_analyst
            -> financial_analyst -> valuation_agent
                                      -> risk_analyst -> synthesis_reviewer

``valuation_agent`` reads only ``financial_summary`` / ``peer_comparison`` /
``company_profile``, so it no longer waits for the business, news or peer
branches. ``peer_analyst`` keeps its real dependency on ``business_analysis``.

Concurrency safety (§5.3):
  * every worker gets its **own** SQLAlchemy ``Session`` built from the same
    bind as the caller's session, and closes it afterwards;
  * ``ResearchState`` is mutated **only** on the coordinating thread, so
    parallel branches never write shared state concurrently;
  * list fields declared with a reducer in ``ResearchState`` (``errors``,
    ``warnings``) are concatenated instead of overwritten;
  * no parallel-candidate service writes to the database (verified: no
    ``session.add/flush/commit`` in the analysis / news / financial / peer /
    risk services), so read-only worker sessions cannot conflict;
  * SSE progress events keep their shape — ``started`` is emitted when a node
    is submitted and ``completed`` when it resolves.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Company, NewsArticle, NewsEvent, ResearchReport, RiskAssessment, ValuationResult

logger = get_logger(__name__)
from app.db.repositories.company_repository import get_company_by_ticker, get_or_create_company
from app.db.repositories.document_repository import get_company_documents
from app.db.repositories.filing_repository import get_effective_filings, get_filings
from app.providers.company_info_provider import get_canonical_company_name
from app.utils.metadata_utils import normalize_ticker
from app.graph.nodes.business_analyst import BusinessAnalystNode
from app.graph.nodes.company_profiler import CompanyProfilerNode
from app.graph.nodes.data_availability_checker import DataAvailabilityCheckerNode
from app.graph.nodes.data_fetcher import DataFetcherNode
from app.graph.nodes.financial_analyst import FinancialAnalystNode
from app.graph.nodes.news_collector import NewsAnalystNode
from app.graph.nodes.peer_analyzer import PeerAnalystNode
from app.graph.nodes.research_planner import ResearchPlannerNode
from app.graph.nodes.risk_analyst import RiskAnalystNode
from app.graph.nodes.synthesis_reviewer import SynthesisReviewerNode
from app.graph.nodes.valuation_agent import ValuationAgentNode
from app.graph.state import ResearchState, create_initial_state
from app.schemas.research import ResearchRunResponse
from app.services import performance_tracker as perf
from app.services.aliyun_llm_service import get_llm_service
from app.services.report_service import ReportService

ProgressCallback = Callable[[str, str, dict[str, Any] | None], None]


@dataclass(frozen=True)
class StepSpec:
    """One workflow node plus the nodes whose outputs it actually consumes."""

    step_id: str
    label: str
    node_cls: type
    depends_on: tuple[str, ...] = ()


WORKFLOW_DAG: list[StepSpec] = [
    StepSpec("research_planner", "ResearchPlanner", ResearchPlannerNode),
    StepSpec("data_availability_checker", "DataAvailabilityChecker", DataAvailabilityCheckerNode, ("research_planner",)),
    StepSpec("data_fetcher", "DataFetcher", DataFetcherNode, ("data_availability_checker",)),
    StepSpec("company_profiler", "CompanyProfiler", CompanyProfilerNode, ("data_fetcher",)),
    # Specialists. peer_analyst consumes business_analysis so it must follow
    # business_analyst; the other three are mutually independent.
    StepSpec("business_analyst", "BusinessAnalyst", BusinessAnalystNode, ("company_profiler",)),
    StepSpec("news_analyst", "NewsAnalyst", NewsAnalystNode, ("company_profiler",)),
    StepSpec("financial_analyst", "FinancialAnalyst", FinancialAnalystNode, ("company_profiler",)),
    StepSpec("peer_analyst", "PeerAnalyst", PeerAnalystNode, ("business_analyst",)),
    # valuation_agent reads financial_summary / peer_comparison / company_profile
    # only — it does not wait for the business, news or peer branches.
    StepSpec("valuation_agent", "ValuationAgent", ValuationAgentNode, ("financial_analyst",)),
    StepSpec("risk_analyst", "RiskAnalyst", RiskAnalystNode, ("valuation_agent", "news_analyst", "peer_analyst")),
    StepSpec(
        "synthesis_reviewer",
        "SynthesisReviewer",
        SynthesisReviewerNode,
        ("risk_analyst", "business_analyst", "financial_analyst", "peer_analyst", "news_analyst"),
    ),
]

#: Topological order of the DAG. Kept because existing callers and tests import
#: ``WORKFLOW_STEPS``; the runner itself executes ``WORKFLOW_DAG``.
WORKFLOW_STEPS: list[tuple[str, str, type]] = [(s.step_id, s.label, s.node_cls) for s in WORKFLOW_DAG]

#: Nodes eligible to run concurrently. The rest stay on the critical path.
PARALLEL_SPECIALISTS = frozenset({"business_analyst", "news_analyst", "financial_analyst", "peer_analyst"})

#: State sections each node owns, used for section-cache reuse (§6.4).
NODE_OUTPUT_SECTIONS: dict[str, tuple[str, ...]] = {
    "company_profiler": ("company_profile",),
    "business_analyst": ("business_analysis", "segment_analysis", "culture_analysis"),
    "news_analyst": ("company_news", "peer_news", "event_list"),
    "financial_analyst": ("financial_summary", "peer_comparison"),
    "peer_analyst": ("peer_analysis",),
}


def _reducer_fields() -> frozenset[str]:
    """State keys that must be concatenated, not overwritten, when merging."""

    names: set[str] = set()
    for name, annotation in ResearchState.__annotations__.items():
        if get_origin(annotation) is Annotated:
            for extra in get_args(annotation)[1:]:
                if getattr(extra, "__name__", "") == "_merge_lists":
                    names.add(name)
    return frozenset(names)


REDUCER_FIELDS = _reducer_fields()


def merge_state(state: ResearchState, result: dict[str, Any]) -> None:
    """Merge a node result into *state*, honoring declared reducers.

    Plain ``state.update(result)`` silently discards an earlier branch's
    ``errors`` / ``warnings`` when a later branch also returns them. Under real
    parallelism that loss becomes systematic, so list fields annotated with
    ``_merge_lists`` are concatenated instead.
    """

    for key, value in result.items():
        if key in REDUCER_FIELDS and isinstance(value, list):
            state[key] = [*(state.get(key) or []), *value]  # type: ignore[assignment]
        else:
            state[key] = value  # type: ignore[assignment]


@dataclass
class ResearchExecution:
    """Result of a workflow execution."""

    state: ResearchState
    response: ResearchRunResponse


class ResearchRunner:
    """Run the research workflow as a dependency DAG with real parallelism."""

    def __init__(
        self,
        session: Session,
        model: str | None = None,
        session_factory: sessionmaker | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.session = session
        self.model = model
        self.settings = get_settings()
        # ``None`` (as opposed to an explicitly passed factory) means "derive one
        # from the caller's bind"; a derived factory of ``None`` means the bind
        # cannot be shared across threads and specialists stay sequential.
        self._factory_provided = session_factory is not None
        self._session_factory = session_factory if self._factory_provided else self._default_session_factory(session)
        self.max_workers = max_workers or self.settings.max_parallel_workers

    @staticmethod
    def _default_session_factory(session: Session) -> sessionmaker | None:
        """Build a per-worker session factory, or ``None`` when unsafe.

        In-memory SQLite (the test fixture) is served by a single shared
        connection through ``StaticPool``; handing that connection to several
        threads is unsafe. Tests that want to exercise real parallelism pass an
        explicit ``session_factory``.
        """

        try:
            bind = session.get_bind()
        except Exception:  # noqa: BLE001
            return None
        if bind.dialect.name == "sqlite" and not bind.url.database:
            return None
        return sessionmaker(bind=bind, autoflush=False, autocommit=False, expire_on_commit=False)

    @property
    def parallel_enabled(self) -> bool:
        """True only when the bind supports per-thread sessions."""

        return bool(
            self.settings.enable_parallel_specialists
            and self._session_factory is not None
            and self.max_workers > 1
        )

    def run(
        self,
        ticker: str,
        company_name: str,
        progress_callback: ProgressCallback | None = None,
        force_refresh: bool = False,
    ) -> ResearchExecution:
        """Execute the workflow DAG and persist outputs."""

        ticker = normalize_ticker(ticker)
        company_name = get_canonical_company_name(ticker, company_name)
        state = create_initial_state(ticker, company_name)
        state["force_refresh"] = bool(force_refresh)

        def emit(step_id: str, status: str, payload: dict[str, Any] | None) -> None:
            if progress_callback:
                progress_callback(step_id, status, payload)

        performance = self._execute_dag(state, emit, force_refresh=force_refresh)

        company = self._get_or_create_company(ticker, company_name)
        response = self._persist_outputs(company, state)
        emit("workflow", "completed", {"final_rating": response.final_rating, "performance": performance})
        return ResearchExecution(state=state, response=response)

    def run_stream(
        self, ticker: str, company_name: str, force_refresh: bool = False
    ) -> Iterator[dict[str, Any]]:
        """Execute the workflow and yield progress events as they happen.

        Events keep the exact shape the SSE endpoint and frontend already
        consume. Parallel branches now interleave in real completion order
        instead of arriving in a fixed sequence.
        """

        import queue
        import threading

        ticker = normalize_ticker(ticker)
        company_name = get_canonical_company_name(ticker, company_name)
        state = create_initial_state(ticker, company_name)
        state["force_refresh"] = bool(force_refresh)
        yield {"event": "started", "data": {"ticker": ticker, "company_name": company_name}}

        events: queue.Queue = queue.Queue()
        sentinel = object()

        def emit(step_id: str, status: str, payload: dict[str, Any] | None) -> None:
            events.put((step_id, status, payload))

        def worker() -> None:
            try:
                performance = self._execute_dag(state, emit, force_refresh=force_refresh)
                events.put(("__performance__", "completed", performance))
            except BaseException as exc:  # noqa: BLE001
                logger.error("research_stream_worker_failed", error=str(exc)[:300])
                events.put(("workflow", "failed", {"error": str(exc)[:500]}))
            finally:
                events.put(sentinel)

        thread = threading.Thread(target=worker, name="research-stream", daemon=True)
        thread.start()
        while True:
            item = events.get()
            if item is sentinel:
                break
            step_id, status, payload = item
            if step_id == "__performance__":
                yield {"event": "performance", "data": payload}
                continue
            yield {"event": "progress", "data": {"step": step_id, "status": status, "payload": payload}}
        thread.join()

        company = self._get_or_create_company(ticker, company_name)
        response = self._persist_outputs(company, state)
        yield {"event": "result", "data": response.model_dump()}
        yield {"event": "done", "data": {"ticker": response.ticker}}

    #: Nodes whose cached sections may be reused instead of recomputed (§6.4).
    #: ``news_analyst`` is excluded on purpose — news is time-sensitive and the
    #: freshness decision already lives in DataAvailabilityChecker. Valuation and
    #: risk are excluded because they embed the current market price (§6.3).
    CACHE_SKIPPABLE_NODES = frozenset(
        {"company_profiler", "business_analyst", "financial_analyst", "peer_analyst"}
    )

    def _execute_dag(
        self,
        state: ResearchState,
        emit: Callable[[str, str, dict[str, Any] | None], None],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Execute ``WORKFLOW_DAG``, running independent branches concurrently.

        State is only ever mutated here, on the coordinating thread; workers
        receive an immutable shallow snapshot and return a result dict.
        """

        specs = {spec.step_id: spec for spec in WORKFLOW_DAG}
        order = [spec.step_id for spec in WORKFLOW_DAG]
        blocked_by: dict[str, set[str]] = {sid: set(specs[sid].depends_on) for sid in order}
        nodes: dict[str, Any] = {}

        tracking = bool(self.settings.enable_performance_tracking)
        task_id = perf.current_run_id()
        if tracking and not task_id:
            task_id = perf.start_run(state.get("ticker", "")).task_id
        if tracking:
            run = perf.get_run(task_id)
            if run is not None:
                run.ticker = state.get("ticker", "") or run.ticker
                run.company_id = (state.get("_context") or {}).get("company_id") or run.company_id

        parallel = self.parallel_enabled
        max_workers = max(1, min(self.max_workers, len(PARALLEL_SPECIALISTS)))
        executor = (
            ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="specialist") if parallel else None
        )
        inflight: dict[Future, str] = {}
        logger.info(
            "research_dag_start",
            ticker=state.get("ticker"),
            parallel=parallel,
            max_workers=max_workers if parallel else 1,
            task_id=task_id,
        )

        try:
            while blocked_by or inflight:
                for sid in [s for s in order if s in blocked_by and not blocked_by[s]]:
                    del blocked_by[sid]
                    spec = specs[sid]
                    skip_reason = self._skip_reason(spec, state, force_refresh)
                    if skip_reason:
                        self._finish_step(sid, None, {}, state, nodes, emit, blocked_by, skipped=skip_reason)
                        continue
                    if executor is not None and sid in PARALLEL_SPECIALISTS:
                        emit(sid, "started", {"label": spec.label, "parallel": True})
                        inflight[executor.submit(self._run_step, spec, dict(state), task_id)] = sid
                    else:
                        emit(sid, "started", {"label": spec.label})
                        step_id, node, result = self._run_step(spec, dict(state), task_id)
                        self._finish_step(step_id, node, result, state, nodes, emit, blocked_by)

                if not inflight:
                    continue
                done, _ = wait(set(inflight), return_when=FIRST_COMPLETED)
                for future in done:
                    step_id = inflight.pop(future)
                    resolved_id, node, result = future.result()
                    self._finish_step(resolved_id or step_id, node, result, state, nodes, emit, blocked_by)
        finally:
            if executor is not None:
                executor.shutdown(wait=True)

        summary = perf.finish_run(task_id) if tracking else {}
        if tracking and summary:
            self._persist_performance(task_id)
            parallelism = summary.get("parallelism", {})
            logger.info(
                "research_dag_completed",
                ticker=state.get("ticker"),
                total_duration_ms=summary.get("total_duration_ms"),
                max_concurrency=parallelism.get("max_concurrency"),
                saved_by_parallel_ms=parallelism.get("saved_by_parallel_ms"),
                llm_calls=summary.get("llm_call_count"),
                cache_hit_rate=summary.get("cache_hit_rate"),
                skipped=summary.get("skipped_nodes"),
                failed=summary.get("failed_nodes"),
            )
        return summary

    def _run_step(self, spec: StepSpec, state_snapshot: dict[str, Any], task_id: str | None) -> tuple[str, Any, dict]:
        """Execute one node on its own database session and measure it."""

        own_session = self._session_factory is not None and spec.step_id in PARALLEL_SPECIALISTS
        worker_session = self._session_factory() if own_session else self.session
        try:
            # ThreadPoolExecutor does not propagate ContextVars, so the run id is
            # rebound here or worker spans would be dropped from telemetry.
            perf.bind_run(task_id)
            node = self._build_node(spec.step_id, spec.node_cls, session=worker_session)
            with perf.span(spec.step_id):
                result = node(state_snapshot)
            return spec.step_id, node, result if isinstance(result, dict) else {}
        finally:
            if own_session:
                worker_session.close()

    def _finish_step(
        self,
        step_id: str,
        node: Any,
        result: dict[str, Any],
        state: ResearchState,
        nodes: dict[str, Any],
        emit: Callable[[str, str, dict[str, Any] | None], None],
        blocked_by: dict[str, set[str]],
        *,
        skipped: str | None = None,
    ) -> None:
        """Merge one node's output and release everything it was blocking."""

        spec = next((s for s in WORKFLOW_DAG if s.step_id == step_id), None)
        label = spec.label if spec else step_id
        nodes[step_id] = node
        if skipped:
            emit(step_id, "completed", {"label": label, "skipped": True, "reason": skipped, "details": {}})
        else:
            merge_state(state, result)
            if step_id == "news_analyst":
                # Persistence stays on the coordinating thread's session so no
                # two threads ever write through the same Session.
                self._persist_news_and_events(state)
            self._commit_checkpoint(step_id)
            emit(
                step_id,
                "completed",
                {
                    "label": label,
                    "errors": state.get("errors", [])[-3:],
                    "details": self._build_step_details(step_id, node, state),
                },
            )
        for dependents in blocked_by.values():
            dependents.discard(step_id)

    def _skip_reason(self, spec: StepSpec, state: ResearchState, force_refresh: bool) -> str | None:
        """Return why a node can be skipped, or ``None`` when it must run (§6.4)."""

        if force_refresh or not self.settings.enable_section_cache_reuse:
            return None
        if spec.step_id not in self.CACHE_SKIPPABLE_NODES:
            return None
        sections = NODE_OUTPUT_SECTIONS.get(spec.step_id, ())
        if not sections:
            return None
        # Reuse only when every section the node owns is already populated.
        # DataAvailabilityChecker restores cached sections into state beforehand.
        if all(bool(state.get(section)) for section in sections):
            return f"section_cache_reuse:{'+'.join(sections)}"
        return None

    def _persist_performance(self, task_id: str | None) -> None:
        """Persist telemetry on a dedicated session.

        A separate session means a telemetry failure can never roll back the
        research transaction, and a missing ``node_executions`` table can never
        break a run.
        """

        if not task_id or not self.settings.enable_performance_tracking:
            return
        run = perf.get_run(task_id)
        if run is None or not run.spans:
            return
        session: Session | None = None
        try:
            if self._session_factory is not None:
                session = self._session_factory()
            else:
                from app.core.database import SessionLocal

                session = SessionLocal()
            perf.persist_run(session, run)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("perf_persist_skipped", task_id=task_id, error=str(exc)[:200])
            if session is not None:
                try:
                    session.rollback()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if session is not None:
                session.close()


    def _build_node(self, step_id: str, node_cls: type, session: Session | None = None) -> Any:
        """Build a workflow step node with the configured model.

        ``session`` lets a parallel worker supply its own session; it defaults to
        the runner's session so sequential behaviour is unchanged.
        """
        db = session or self.session
        if step_id == "research_planner":
            return node_cls(db, model=self.model)
        if step_id in {"data_availability_checker", "data_fetcher"}:
            return node_cls(db)
        if step_id == "company_profiler":
            # NOTE: deliberately does not pass skip_ingestion, preserving the
            # pre-optimization runner behaviour exactly. workflow.py builds this
            # node with skip_ingestion=True; reconciling the two is recorded as
            # a follow-up rather than changed silently here.
            return node_cls(db, model=self.model)
        if step_id in {"business_analyst", "news_analyst"}:
            return node_cls(db, model=self.model)
        if step_id == "peer_analyst":
            return node_cls(session=db, model=self.model)
        if step_id in {"financial_analyst", "risk_analyst"}:
            return node_cls(db)
        if step_id == "valuation_agent":
            # Session lets the valuation skill reuse a persisted result whose
            # recorded cache key still matches current inputs (§6.3).
            return node_cls(session=db)
        if step_id == "synthesis_reviewer":
            return node_cls(db, model=self.model)
        return node_cls()

    def _get_or_create_company(self, ticker: str, company_name: str) -> Company:
        return get_or_create_company(self.session, ticker, company_name)

    def _persist_news_and_events(self, state: ResearchState) -> None:
        """Store fetched news and extracted events for later reuse."""

        company = self._get_or_create_company(state["ticker"], state["company_name"])
        article_lookup: dict[tuple[str, str], NewsArticle] = {}
        for article in state.get("company_news", []):
            stored = self._upsert_article(company, article)
            for key in self._article_lookup_keys(article):
                article_lookup[(article.get("ticker", "").upper(), key)] = stored

        for peer in state.get("peer_list", []):
            peer_ticker = str(peer.get("ticker", "")).upper()
            if not peer_ticker:
                continue
            peer_company = self._get_or_create_company(peer_ticker, peer.get("company_name") or peer_ticker)
            for article in [item for item in state.get("peer_news", []) if str(item.get("ticker", "")).upper() == peer_ticker]:
                stored = self._upsert_article(peer_company, article)
                for key in self._article_lookup_keys(article):
                    article_lookup[(peer_ticker, key)] = stored

        for event in state.get("event_list", []):
            event_ticker = str(event.get("ticker") or state["ticker"]).upper()
            event_company = self._get_or_create_company(
                event_ticker,
                state["company_name"] if event_ticker == state["ticker"] else event_ticker,
            )
            event_key = (
                event_company.id,
                str(event.get("event_type", "")),
                str(event.get("event_summary", "")),
                self._coerce_date(event.get("event_date")),
            )
            existing = self.session.scalar(
                select(NewsEvent).where(
                    NewsEvent.company_id == event_key[0],
                    NewsEvent.event_type == event_key[1],
                    NewsEvent.event_summary == event_key[2],
                    NewsEvent.event_date == event_key[3],
                )
            )
            if existing:
                continue
            article_ref = article_lookup.get((event_ticker, str(event.get("event_summary", "")))) or article_lookup.get(
                (event_ticker, str(event.get("title", "")))
            )
            self.session.add(
                NewsEvent(
                    article_id=article_ref.id if article_ref else None,
                    company_id=event_company.id,
                    event_date=event_key[3],
                    event_type=event_key[1] or "other",
                    sentiment=event.get("sentiment"),
                    importance_score=event.get("importance_score"),
                    confidence_score=event.get("confidence_score"),
                    risk_tags=event.get("risk_tags", []),
                    entities=event.get("entities", []),
                    event_summary=event_key[2],
                    impact_dimensions=event.get("impact_dimensions", {}),
                    created_at=datetime.now(timezone.utc),
                )
            )
        self.session.flush()

    def _upsert_article(self, company: Company, article: dict[str, Any]) -> NewsArticle:
        key_url = self._normalize_article_url(article.get("url"))
        key_title = self._safe_varchar(article.get("title"), 500)
        existing = None
        if key_url:
            existing = self.session.scalar(
                select(NewsArticle).where(NewsArticle.company_id == company.id, NewsArticle.url == key_url)
            )
        if existing is None and key_title:
            existing = self.session.scalar(
                select(NewsArticle).where(NewsArticle.company_id == company.id, NewsArticle.title == key_title)
            )
        published_at = self._coerce_datetime(article.get("published_at"))
        if existing:
            existing.summary = self._safe_text(article.get("summary"))
            existing.content = self._safe_text(article.get("content"))
            existing.source_name = self._safe_varchar(article.get("source_name"), 128)
            existing.source_type = self._safe_varchar(article.get("source_type"), 32) or existing.source_type
            existing.author = self._safe_varchar(article.get("author"), 128)
            existing.language = self._safe_varchar(article.get("language", "en"), 32)
            existing.published_at = published_at or existing.published_at
            existing.fetched_at = datetime.now(timezone.utc)
            existing.url = key_url or existing.url
            return existing

        stored = NewsArticle(
            company_id=company.id,
            title=key_title or "Untitled Article",
            source_name=self._safe_varchar(article.get("source_name"), 128),
            source_type=self._safe_varchar(article.get("source_type"), 32) or "finance_news",
            url=key_url,
            published_at=published_at,
            author=self._safe_varchar(article.get("author"), 128),
            content=self._safe_text(article.get("content")),
            summary=self._safe_text(article.get("summary")),
            language=self._safe_varchar(article.get("language", "en"), 32),
            raw_html=self._safe_text(article.get("raw_html")),
            fetched_at=datetime.now(timezone.utc),
        )
        self.session.add(stored)
        self.session.flush()
        return stored

    def _persist_outputs(self, company: Company, state: ResearchState) -> ResearchRunResponse:
        from app.services.section_cache_service import SectionCacheService

        report_markdown = state.get("final_report", "")
        report_json = state.get("report_json", {})
        final_rating = state.get("final_rating", "research_more")
        if not report_markdown or not report_json:
            report_markdown, report_json, final_rating = ReportService().build(state, model=self.model)

        report_json = SectionCacheService().save_section_cache(report_json, state)

        # V3: embed research plan in report metadata for traceability
        if "research_plan" not in report_json:
            report_json["_research_plan"] = state.get("research_plan")

        valuation = state.get("valuation_result", {})
        blended = valuation.get("blended", {})
        self.session.add(
            ValuationResult(
                company_id=company.id,
                valuation_date=date.today(),
                valuation_method="blended_per_share_v2",
                bull_value=blended.get("bull"),
                base_value=blended.get("base"),
                bear_value=blended.get("bear"),
                assumptions=state.get("valuation_assumptions", {}),
                sensitivity={
                    "methods": list(valuation.keys()),
                    "per_share_currency": valuation.get("per_share_currency"),
                    "shares_outstanding": valuation.get("shares_outstanding"),
                },
                conclusion=f"Per-share valuation in {valuation.get('per_share_currency', 'N/A')}.",
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.add(
            RiskAssessment(
                company_id=company.id,
                assessment_date=date.today(),
                fundamentals_risk_score=state.get("risk_assessment", {}).get("fundamentals_risk_score"),
                competition_risk_score=state.get("risk_assessment", {}).get("competition_risk_score"),
                governance_risk_score=state.get("risk_assessment", {}).get("governance_risk_score"),
                event_risk_score=state.get("risk_assessment", {}).get("event_risk_score"),
                valuation_risk_score=state.get("risk_assessment", {}).get("valuation_risk_score"),
                overall_risk_score=state.get("risk_assessment", {}).get("overall_risk_score"),
                red_flags=state.get("risk_assessment", {}).get("red_flags", []),
                key_risks=state.get("risk_assessment", {}).get("key_risks", []),
                monitoring_signals=state.get("risk_assessment", {}).get("monitoring_signals", []),
                recommendation=state.get("risk_assessment", {}).get("recommendation", ""),
                supporting_evidence=state.get("risk_assessment", {}).get("supporting_evidence", []),
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.add(
            ResearchReport(
                company_id=company.id,
                report_date=date.today(),
                title=f"Investment Research Report - {company.company_name} ({company.ticker})",
                executive_summary=str(report_json.get("executive_summary", "")),
                report_markdown=report_markdown,
                report_json=report_json,
                final_rating=final_rating,
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.commit()
        self._save_report_files(company.ticker, report_markdown, report_json)
        return ResearchRunResponse(
            ticker=company.ticker,
            company_name=company.company_name,
            final_rating=final_rating,
            executive_summary=str(report_json.get("executive_summary", "")),
            report_markdown=report_markdown,
            report_json=report_json,
        )

    def _save_report_files(self, ticker: str, report_markdown: str, report_json: dict) -> None:
        """Save report as .docx and .md to data/results/{market}/{ticker}/."""

        from app.core.config import DATA_ROOT
        from app.services.docx_export_service import markdown_report_to_docx
        from app.utils.metadata_utils import infer_market_from_ticker

        market = infer_market_from_ticker(ticker)
        results_dir = DATA_ROOT / "results" / market / ticker
        results_dir.mkdir(parents=True, exist_ok=True)
        date_str = date.today().isoformat()
        stem = f"{ticker.replace('.', '_')}_{date_str}"

        md_path = results_dir / f"{stem}.md"
        md_path.write_text(report_markdown, encoding="utf-8")

        try:
            buf = markdown_report_to_docx(report_markdown, report_json)
            docx_path = results_dir / f"{stem}.docx"
            docx_path.write_bytes(buf.getvalue())
            logger.info("report_saved", ticker=ticker, md=str(md_path), docx=str(docx_path))
        except Exception as exc:
            logger.warning("docx_save_failed", ticker=ticker, error=str(exc))

    def _commit_checkpoint(self, step_id: str) -> None:
        """Persist step-level side effects early so streaming runs don't lose filings/news on later interruption."""

        if step_id not in {"research_planner", "company_profiler", "news_analyst", "data_fetcher"}:
            return
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        self.session.commit()
        self.session.expire_all()

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and value:
            normalized = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def _article_lookup_keys(self, article: dict[str, Any]) -> list[str]:
        """Generate candidate lookup keys for article-to-event linking."""

        keys = [article.get("summary"), article.get("title"), article.get("url")]
        return [str(item) for item in keys if item]

    def _safe_varchar(self, value: Any, limit: int) -> str | None:
        """Trim string values to fit VARCHAR columns."""

        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:limit]

    def _safe_text(self, value: Any) -> str | None:
        """Normalize optional text payloads."""

        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_article_url(self, value: Any) -> str | None:
        """Shorten noisy aggregator URLs so they fit storage and dedupe better."""

        raw = self._safe_text(value)
        if raw is None:
            return None

        parsed = urlparse(raw)
        if "news.google.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            for key in ("url", "u"):
                candidate = qs.get(key, [None])[0]
                if candidate:
                    normalized = self._safe_text(unquote(candidate))
                    if normalized:
                        return normalized[:500]
        if len(raw) <= 500:
            return raw

        shortened = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else raw
        if len(shortened) <= 500:
            return shortened
        return shortened[:500]

    def _build_step_details(self, step_id: str, node: Any, state: ResearchState) -> dict[str, Any]:
        """Build human-friendly execution details for the frontend run log."""

        if step_id == "research_planner":
            plan = state.get("research_plan", {})
            sections = plan.get("sections", {})
            return {
                "model": plan.get("model"),
                "section_count": len(sections),
                "active_sections": [k for k, v in sections.items() if not v.get("skip")],
                "skipped_sections": [k for k, v in sections.items() if v.get("skip")],
            }
        if step_id == "data_availability_checker":
            da = state.get("data_availability", {})
            return {
                "has_financials": da.get("has_financials", False),
                "has_filings": da.get("has_filings", False),
                "has_news": da.get("has_news", False),
                "has_documents": da.get("has_documents", False),
                "has_profile": da.get("has_profile", False),
                "total_filing_count": da.get("total_filing_count", 0),
            }
        if step_id == "data_fetcher":
            # ``fetch_results`` is the declared state key written by the node;
            # the legacy ``data_fetch_result`` shape no longer exists.
            fr = state.get("fetch_results", {})
            peers_fetched = fr.get("peer_filings_fetched") or []
            return {
                "target_filings_fetched": bool(fr.get("target_filings_fetched")),
                "peer_filings_fetched": len(peers_fetched),
                "news_fetched": bool(fr.get("news_fetched")),
                "fetch_errors": len(fr.get("fetch_errors") or []),
            }
        if step_id == "company_profiler":
            provider = getattr(getattr(node, "service", None), "provider", None)
            raw_sources = state.get("company_profile", {}).get("raw_sources", [])
            llm = getattr(provider, "llm", None)
            company = get_company_by_ticker(self.session, state.get("ticker", ""))
            documents = get_company_documents(self.session, company.id) if company else []
            filings = get_filings(self.session, company.id) if company else []
            effective_filings = get_effective_filings(self.session, company.id) if company else []
            document_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "company_document"]
            evidence_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "document_rag"]
            filing_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "filing"]
            filing_rag_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "filing_rag"]
            document_topics = sorted(
                {
                    topic
                    for item in [*evidence_sources, *filing_rag_sources]
                    for topic in (item.get("topics") or [])
                    if isinstance(topic, str) and topic.strip()
                }
            )
            peer_samples = [
                {
                    "ticker": peer.get("ticker"),
                    "peer_type": peer.get("peer_type"),
                    "rationale": peer.get("rationale"),
                }
                for peer in state.get("peer_list", [])[:4]
                if isinstance(peer, dict)
            ]
            warnings = state.get("company_profile", {}).get("warnings", [])
            filing_status = "enabled"
            if any("skipped filing ingestion" in str(item).lower() or "tables not created" in str(item).lower() for item in warnings):
                filing_status = "degraded"
            elif len(effective_filings) == 0:
                filing_status = "no_results"
            filing_status_label = {
                "enabled": "Enabled",
                "degraded": "Degraded",
                "no_results": "Enabled, but no usable disclosure files found yet",
            }.get(filing_status, filing_status)
            return {
                "provider": provider.__class__.__name__ if provider else None,
                "sources": raw_sources,
                "peer_count": len(state.get("peer_list", [])),
                "is_mock": any("mock" in str(item.get("source", "")).lower() for item in raw_sources if isinstance(item, dict)),
                "model": self._model_info(llm),
                "warnings": warnings,
                "filing_status": filing_status,
                "filing_status_label": filing_status_label,
                "document_count": len(documents),
                "filing_count": len(effective_filings),
                "filing_total_count": len(filings),
                "document_types": sorted({str(item.doc_type) for item in documents if item.doc_type}),
                "filing_types": sorted({str(item.filing_type) for item in effective_filings if item.filing_type}),
                "document_years": [item.fiscal_year for item in documents if item.fiscal_year is not None][:8],
                "filing_years": [item.fiscal_year for item in effective_filings if item.fiscal_year is not None][:8],
                "document_sources": [
                    {
                        "doc_type": item.get("doc_type"),
                        "fiscal_year": item.get("fiscal_year"),
                        "source_name": item.get("source_name"),
                    }
                    for item in document_sources[:8]
                ],
                "filing_sources": [
                    {
                        "filing_type": item.get("filing_type"),
                        "fiscal_year": item.get("fiscal_year"),
                        "source_name": item.get("source_name"),
                    }
                    for item in filing_sources[:8]
                ],
                "effective_filing_sources": [
                    {
                        "filing_type": item.filing_type,
                        "fiscal_year": item.fiscal_year,
                        "source_name": item.source_name,
                        "file_type": item.file_type,
                    }
                    for item in effective_filings[:8]
                ],
                "evidence_topics": document_topics,
                "evidence_bundle_count": len(evidence_sources) + len(filing_rag_sources),
                "peer_samples": peer_samples,
                "profile_source": "annual_report_only" if effective_filings else "unavailable",
            }
        if step_id == "news_analyst":
            articles = state.get("company_news", []) + state.get("peer_news", [])
            source_names = sorted({str(item.get("source_name")) for item in articles if item.get("source_name")})
            source_types = sorted({str(item.get("source_type")) for item in articles if item.get("source_type")})
            return {
                "company_news_count": len(state.get("company_news", [])),
                "peer_news_count": len(state.get("peer_news", [])),
                "event_count": len(state.get("event_list", [])),
                "sources": source_names,
                "source_names": source_names,
                "source_types": source_types,
                "is_mock": any(self._article_is_mock(item) for item in articles),
            }
        if step_id == "financial_analyst":
            summary = state.get("financial_summary", {})
            return {
                "provider": summary.get("provider_name"),
                "provider_name": summary.get("provider_name"),
                "data_source": summary.get("data_source_label"),
                "source_detail": summary.get("source_detail", {}),
                "years": summary.get("years", []),
                "years_count": len(summary.get("years", [])),
                "red_flags": state.get("financial_red_flags", []),
                "data_status": "unavailable" if summary.get("data_source") == "unavailable" else "available",
                "is_mock": False,
            }
        if step_id == "valuation_agent":
            valuation = state.get("valuation_result", {})
            supplemental = valuation.get("legacy_value_analysis", {})
            blended = valuation.get("blended", {})
            return {
                "methods": list(valuation.keys()),
                "per_share_currency": valuation.get("per_share_currency"),
                "blended_bear": blended.get("bear"),
                "blended_base": blended.get("base"),
                "blended_bull": blended.get("bull"),
                "primary_base": valuation.get("primary_value", {}).get("base"),
                "growth_base": valuation.get("growth", {}).get("base"),
                "shares_outstanding": valuation.get("shares_outstanding"),
                "supplemental_method": supplemental.get("method"),
                "supplemental_status": supplemental.get("status"),
                "supplemental_lower": supplemental.get("lower_price_limit"),
                "supplemental_upper": supplemental.get("upper_price_limit"),
                "is_mock": False,
            }
        if step_id == "risk_analyst":
            risk = state.get("risk_assessment", {})
            return {
                "overall_risk_score": risk.get("overall_risk_score"),
                "recommendation": risk.get("recommendation"),
                "red_flag_count": len(risk.get("red_flags", [])),
                "is_mock": False,
            }
        if step_id == "business_analyst":
            ba = state.get("business_analysis", {})
            sa = state.get("segment_analysis", {})
            return {
                "has_business_analysis": bool(ba.get("core_judgement")),
                "is_group_company": sa.get("is_group_company", False),
                "segment_count": len(sa.get("segments", [])),
            }
        if step_id == "peer_analyst":
            pa = state.get("peer_analysis", {})
            snapshots = pa.get("peer_metric_snapshots", {})
            return {
                "core_peer_count": len(pa.get("core_peers", [])),
                "secondary_peer_count": len(pa.get("secondary_peers", [])),
                "valuation_peer_count": len(pa.get("valuation_peers", [])),
                "peer_metric_count": len(snapshots),
                "peer_metric_source": next(
                    (v.get("source", "local_financial") for v in snapshots.values()),
                    None,
                ) if snapshots else None,
            }
        if step_id == "synthesis_reviewer":
            sr = state.get("synthesis_review", {})
            return {
                "passed": sr.get("passed", False),
                "missing_sections": sr.get("missing_sections", []),
                "quality_issues": sr.get("quality_issues", []),
                "recommendations": sr.get("recommendations", []),
            }
        return {}

    def _model_info(self, llm: Any) -> dict[str, Any] | None:
        """Return logging-safe LLM info."""

        if llm is None:
            return None
        config = getattr(llm, "config", None)
        if config is None:
            return {"enabled": bool(getattr(llm, "available", False))}
        return {
            "enabled": bool(getattr(llm, "available", False)),
            "provider": getattr(config, "provider", None),
            "model": getattr(config, "model", None),
            "base_url": getattr(config, "base_url", None),
        }

    def _article_is_mock(self, article: dict[str, Any]) -> bool:
        """Detect whether an article likely comes from a mock source."""

        source_name = str(article.get("source_name", "")).lower()
        url = str(article.get("url", "")).lower()
        return source_name.startswith("mock") or "example.com" in url

    def _coerce_date(self, value: Any) -> date | None:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        parsed = self._coerce_datetime(value)
        return parsed.date() if parsed else None