"""financial_report_ingestion_skill — filing download / parse / index orchestration.

Optimization task §4.1. The heavy lifting already exists and is **not**
re-implemented here: :class:`FilingService._ingest_one_filing` already

  * skips non-report notices and stub candidates,
  * reuses a filing that is downloaded *and* chunked,
  * re-parses a downloaded-but-unparsed file **without re-downloading**,
  * detects duplicates by checksum,
  * initializes the vector store lazily, only when a parse actually happens.

What this skill adds on top:

1. The unified return payload required by §4.1 (available / downloaded /
   reused / parsed / indexed / failed / warnings).
2. **Cross-task deduplication** — an in-process registry so two concurrent
   research runs requesting the same company's annual report share one
   ingestion instead of downloading it twice (§7.1).
3. Separate telemetry for discovery vs download+parse (§10.1 question 4).
4. An explicit ``index_vectors=False`` path so structured financial analysis is
   never blocked by vector indexing (§4.1).
5. Bounded retry for transient download failures only (§7.2).

Counts are derived by snapshotting the persisted ``Filing`` rows before and
after the existing service call — non-invasive and always consistent with the
database rather than with an internal counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Filing
from app.db.repositories.company_repository import get_or_create_company
from app.db.repositories.filing_repository import get_effective_filings, get_filings
from app.providers.company_info_provider import get_canonical_company_name
from app.providers.vector_store_provider import NullVectorStoreProvider
from app.services.filing_service import FilingService
from app.skills.base import retry_transient, skill_span
from app.utils.metadata_utils import infer_market_from_ticker, normalize_ticker

logger = get_logger(__name__)

DEFAULT_FILING_TYPES = frozenset({"annual_report"})
DEFAULT_YEARS = 1

# In-process registry of ingestions currently running, keyed by a stable
# identity string. Lets concurrent tasks coalesce onto one download.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}
_INFLIGHT_WAIT_SECONDS = 600.0


def _inflight_key(company_id: int, filing_types: frozenset[str], years: int, index_vectors: bool) -> str:
    return f"{company_id}|{years}|{','.join(sorted(filing_types))}|{int(index_vectors)}"


@dataclass
class FilingTaskStatus:
    """Per-filing outcome, derived from persisted row state."""

    filing_id: int | None
    ticker: str
    filing_type: str | None
    fiscal_year: int | None
    file_url: str | None
    download_status: str | None
    parse_status: str | None
    chunk_count: int
    #: downloaded | reused | reparsed | failed_download | failed_parse | unchanged
    action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "filing_id": self.filing_id,
            "ticker": self.ticker,
            "filing_type": self.filing_type,
            "fiscal_year": self.fiscal_year,
            "file_url": self.file_url,
            "download_status": self.download_status,
            "parse_status": self.parse_status,
            "chunk_count": self.chunk_count,
            "action": self.action,
        }


@dataclass
class IngestionResult:
    """Unified ingestion payload (§4.1)."""

    ticker: str
    company_id: int | None = None
    company_name: str | None = None
    status: str = "succeeded"  # succeeded | partial | failed | reused | skipped
    available_filings: list[dict] = field(default_factory=list)
    downloaded_filings: list[str] = field(default_factory=list)
    reused_filings: list[str] = field(default_factory=list)
    parsed_filings: list[str] = field(default_factory=list)
    indexed_filings: list[str] = field(default_factory=list)
    failed_tasks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tasks: list[FilingTaskStatus] = field(default_factory=list)
    #: True when another in-flight task produced this result for us.
    coalesced: bool = False
    index_vectors: bool = True

    @property
    def effective_count(self) -> int:
        return len(self.available_filings)

    def counts(self) -> dict[str, int]:
        return {
            "available": len(self.available_filings),
            "downloaded": len(self.downloaded_filings),
            "reused": len(self.reused_filings),
            "parsed": len(self.parsed_filings),
            "indexed": len(self.indexed_filings),
            "failed": len(self.failed_tasks),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company_id": self.company_id,
            "status": self.status,
            "counts": self.counts(),
            "available_filings": self.available_filings,
            "downloaded_filings": self.downloaded_filings,
            "reused_filings": self.reused_filings,
            "parsed_filings": self.parsed_filings,
            "indexed_filings": self.indexed_filings,
            "failed_tasks": self.failed_tasks,
            "warnings": self.warnings,
            "coalesced": self.coalesced,
            "index_vectors": self.index_vectors,
        }


class FinancialReportIngestionSkill:
    """On-demand filing ingestion with cross-task deduplication.

    Two service instances are held: the default one (vector indexing on) for the
    target company, and a ``NullVectorStoreProvider`` one for peers whose chunks
    only need to live in PostgreSQL. This mirrors the split ``DataFetcherNode``
    already used, but exposes it as an explicit ``index_vectors`` flag.
    """

    def __init__(
        self,
        filing_service: FilingService | None = None,
        no_index_filing_service: FilingService | None = None,
    ) -> None:
        self.filing_service = filing_service or FilingService()
        self.no_index_filing_service = no_index_filing_service or FilingService(
            vector_store=NullVectorStoreProvider()
        )

    def ingest(
        self,
        session: Session,
        ticker: str,
        company_name: str | None = None,
        *,
        years: int = DEFAULT_YEARS,
        filing_types: set[str] | None = None,
        index_vectors: bool = True,
        force_refresh: bool = False,
        attempts: int = 2,
    ) -> IngestionResult:
        """Ingest filings for one company, coalescing concurrent duplicates."""

        if not ticker:
            raise ValueError("financial_report_ingestion_skill requires a non-empty ticker")
        ticker = normalize_ticker(ticker)
        canonical_name = get_canonical_company_name(ticker, company_name or ticker)
        types = frozenset(filing_types or DEFAULT_FILING_TYPES)

        with skill_span("financial_report_ingestion") as record:
            company = get_or_create_company(session, ticker, canonical_name)
            record.skill_name = f"financial_report_ingestion:{ticker}"
            key = _inflight_key(company.id, types, years, index_vectors)

            # --- §7.1 dedup: join an identical ingestion already in flight ----
            with _INFLIGHT_LOCK:
                pending = _INFLIGHT.get(key)
                owner = pending is None
                if owner:
                    _INFLIGHT[key] = threading.Event()
            if not owner and pending is not None:
                logger.info("filing_ingestion_coalesced", ticker=ticker, key=key)
                pending.wait(_INFLIGHT_WAIT_SECONDS)
                session.expire_all()
                result = self._observe(session, ticker, company.id, canonical_name, index_vectors)
                result.coalesced = True
                result.status = "reused"
                record.cache_hit = True
                record.detail = "coalesced_onto_inflight_task"
                return result

            try:
                result = retry_transient(
                    lambda: self._run_ingestion(
                        session,
                        company,
                        ticker,
                        canonical_name,
                        years=years,
                        filing_types=set(types),
                        index_vectors=index_vectors,
                        force_refresh=force_refresh,
                    ),
                    attempts=attempts,
                    on_retry=lambda attempt, exc: (
                        record.__setattr__("retry_count", attempt),
                        logger.warning(
                            "filing_ingestion_retry", ticker=ticker, attempt=attempt, error=str(exc)[:160]
                        ),
                    ),
                )
            finally:
                with _INFLIGHT_LOCK:
                    event = _INFLIGHT.pop(key, None)
                if event is not None:
                    event.set()
            return result

    def ingest_many(
        self,
        session: Session,
        tickers: list[str],
        *,
        years: int = DEFAULT_YEARS,
        filing_types: set[str] | None = None,
        index_vectors: bool = False,
    ) -> dict[str, IngestionResult]:
        """Ingest several companies (typically peers) without failing as a group.

        Each ticker is independent: one failure is recorded in that ticker's
        result and never aborts the others (§7.2).
        """

        results: dict[str, IngestionResult] = {}
        for raw in tickers:
            ticker = normalize_ticker(raw)
            if not ticker or ticker in results:
                continue
            try:
                results[ticker] = self.ingest(
                    session,
                    ticker,
                    ticker,
                    years=years,
                    filing_types=filing_types,
                    index_vectors=index_vectors,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("filing_ingestion_ticker_failed", ticker=ticker, error=str(exc)[:200])
                failed = IngestionResult(ticker=ticker, status="failed", index_vectors=index_vectors)
                failed.failed_tasks.append({"ticker": ticker, "error": str(exc)[:300]})
                failed.warnings.append(f"Ingestion failed for {ticker}: {exc}")
                results[ticker] = failed
        return results


    # ----------------------------------------------------------------- internal

    def _run_ingestion(
        self,
        session: Session,
        company: Any,
        ticker: str,
        company_name: str,
        *,
        years: int,
        filing_types: set[str],
        index_vectors: bool,
        force_refresh: bool,
    ) -> IngestionResult:
        """Snapshot rows, delegate to the existing service, then diff the rows."""

        before = self._snapshot(session, company.id)
        service = self.filing_service if index_vectors else self.no_index_filing_service
        market = infer_market_from_ticker(ticker)

        if force_refresh:
            # Existing capability: rebuild strictly from already-downloaded local
            # files. A forced refresh never implies re-downloading.
            with skill_span("filing_index_rebuild", span_kind="io") as rebuild:
                try:
                    rebuilt = service.rebuild_local_filing_index(session, company, filing_types or None)
                    rebuild.detail = f"processed={rebuilt.get('processed')} skipped={rebuilt.get('skipped')}"
                except Exception as exc:  # noqa: BLE001
                    rebuild.status = "failed"
                    logger.warning("filing_index_rebuild_failed", ticker=ticker, error=str(exc)[:200])

        with skill_span("filing_discovery_and_ingest", span_kind="io") as io_span:
            filings, warnings = service.ingest_company_filings(
                session=session,
                company=company,
                market=market,
                years=years,
                filing_types=filing_types or None,
            )
            io_span.detail = f"rows={len(filings)}"

        after = self._snapshot(session, company.id)
        result = self._build_result(session, ticker, company.id, company_name, before, after, warnings, index_vectors)
        logger.info(
            "filing_ingestion_skill_done",
            ticker=ticker,
            status=result.status,
            counts=result.counts(),
            warnings=len(result.warnings),
        )
        return result

    @staticmethod
    def _snapshot(session: Session, company_id: int) -> dict[int, dict[str, Any]]:
        """Capture persisted filing state so actions can be derived by diffing."""

        snapshot: dict[int, dict[str, Any]] = {}
        for filing in get_filings(session, company_id):
            snapshot[filing.id] = {
                "ticker": filing.ticker,
                "filing_type": filing.filing_type,
                "fiscal_year": filing.fiscal_year,
                "file_url": filing.file_url,
                "download_status": filing.download_status,
                "parse_status": filing.parse_status,
                "chunk_count": int((filing.metadata_json or {}).get("chunk_count") or 0),
            }
        return snapshot


    def _observe(
        self, session: Session, ticker: str, company_id: int, company_name: str, index_vectors: bool
    ) -> IngestionResult:
        """Build a result from current DB state without running ingestion."""

        after = self._snapshot(session, company_id)
        return self._build_result(session, ticker, company_id, company_name, {}, after, [], index_vectors)

    def _build_result(
        self,
        session: Session,
        ticker: str,
        company_id: int | None,
        company_name: str,
        before: dict[int, dict[str, Any]],
        after: dict[int, dict[str, Any]],
        warnings: list[str],
        index_vectors: bool,
    ) -> IngestionResult:
        """Classify each filing row by comparing before/after persisted state."""

        result = IngestionResult(
            ticker=ticker, company_id=company_id, company_name=company_name, index_vectors=index_vectors
        )
        for filing_id, now in after.items():
            prior = before.get(filing_id)
            parsed_now = now["parse_status"] == "parsed" and now["chunk_count"] > 0
            downloaded_now = now["download_status"] == "downloaded"

            if prior is None:
                action = "downloaded" if downloaded_now else "failed_download"
            elif now["chunk_count"] > prior["chunk_count"] or (parsed_now and prior["parse_status"] != "parsed"):
                action = "reparsed" if prior.get("download_status") == "downloaded" else "downloaded"
            elif parsed_now:
                action = "reused"
            elif downloaded_now or now["download_status"] == "failed":
                action = "failed_parse" if downloaded_now else "failed_download"
            else:
                action = "unchanged"

            task = FilingTaskStatus(
                filing_id=filing_id,
                ticker=now["ticker"],
                filing_type=now["filing_type"],
                fiscal_year=now["fiscal_year"],
                file_url=now["file_url"],
                download_status=now["download_status"],
                parse_status=now["parse_status"],
                chunk_count=now["chunk_count"],
                action=action,
            )
            result.tasks.append(task)
            label = f"{now['filing_type']}:{now['fiscal_year']}"

            if action == "downloaded":
                result.downloaded_filings.append(label)
            elif action in {"reused", "unchanged"}:
                result.reused_filings.append(label)
            if action in {"downloaded", "reparsed"} and parsed_now:
                result.parsed_filings.append(label)
                if index_vectors:
                    result.indexed_filings.append(label)
            if action.startswith("failed"):
                result.failed_tasks.append({**task.to_dict(), "error": action})

        if company_id is not None:
            result.available_filings = [
                {
                    "filing_id": f.id,
                    "filing_type": f.filing_type,
                    "fiscal_year": f.fiscal_year,
                    "file_type": f.file_type,
                    "source_name": f.source_name,
                }
                for f in get_effective_filings(session, company_id)
            ]
        result.warnings = list(warnings or [])
        if result.failed_tasks and (result.downloaded_filings or result.reused_filings):
            result.status = "partial"
        elif result.failed_tasks and not result.available_filings:
            result.status = "failed"
        return result


_default_ingestion_skill: FinancialReportIngestionSkill | None = None


def get_ingestion_skill() -> FinancialReportIngestionSkill:
    """Return the process-wide ingestion skill (services are stateless)."""

    global _default_ingestion_skill
    if _default_ingestion_skill is None:
        _default_ingestion_skill = FinancialReportIngestionSkill()
    return _default_ingestion_skill


def ingest_financial_reports(
    session: Session,
    ticker: str,
    company_name: str | None = None,
    *,
    years: int = DEFAULT_YEARS,
    filing_types: set[str] | None = None,
    index_vectors: bool = True,
    force_refresh: bool = False,
) -> IngestionResult:
    """Functional entry point for the financial report ingestion skill."""

    return get_ingestion_skill().ingest(
        session,
        ticker,
        company_name,
        years=years,
        filing_types=filing_types,
        index_vectors=index_vectors,
        force_refresh=force_refresh,
    )

