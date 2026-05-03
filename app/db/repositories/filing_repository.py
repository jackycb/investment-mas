"""Repository helpers for filing metadata and chunks."""

from __future__ import annotations

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Filing, FilingChunk
from app.core.logging import get_logger

logger = get_logger(__name__)

SEARCH_PAGE_SOURCE_NAMES = {
    "hkexnews_search",
    "cninfo_fulltext_search",
    "sec_edgar_full_text_search",
    "sec_edgar_search",
}

SEARCH_PAGE_URL_MARKERS = ("titlesearch.xhtml", "fulltextSearch", "browse-edgar", "/edgar/search/")

NON_REPORT_ANNUAL_NOTICE_MARKERS = (
    "notification letter",
    "reply form",
    "notice of publication",
    "non-registered holder",
    "non-registered shareholder",
    "registered holder",
    "registered shareholder",
    "electronic dissemination",
    "arrangement of electronic dissemination",
    "发布通知",
    "通知信函",
    "回条",
    "非登记",
    "登记股东",
    "以电子方式发布",
)


_filing_tables_cache: dict[int, bool] = {}


def filing_tables_ready(session: Session) -> bool:
    """Return whether filing tables are available in the current database."""

    bind = session.get_bind()
    if bind is None:
        return False
    engine_id = id(bind)
    if _filing_tables_cache.get(engine_id) is True:
        return _filing_tables_cache[engine_id]
    try:
        inspect_target = session.connection() if session.in_transaction() else bind
        inspector = inspect(inspect_target)
        ready = inspector.has_table("filings") and inspector.has_table("filing_chunks")
    except SQLAlchemyError:
        ready = False
    if ready:
        _filing_tables_cache[engine_id] = True
    else:
        _filing_tables_cache.pop(engine_id, None)
    return ready


def get_filings(session: Session, company_id: int) -> list[Filing]:
    """Return filings ordered by latest published/report date."""

    if not filing_tables_ready(session):
        return []
    stmt = (
        select(Filing)
        .where(Filing.company_id == company_id)
        .order_by(Filing.published_at.desc().nullslast(), Filing.report_date.desc().nullslast(), Filing.id.desc())
    )
    try:
        return list(session.scalars(stmt).all())
    except SQLAlchemyError as exc:
        logger.warning("filings_lookup_failed", company_id=company_id, error=str(exc))
        session.rollback()
        return []


def is_effective_filing(filing: Filing) -> bool:
    """Return whether a filing row represents a real usable disclosure file."""

    metadata = filing.metadata_json or {}
    file_url = str(filing.file_url or "")
    title = " ".join(str(filing.title or "").lower().replace("\n", " ").split())

    if metadata.get("stub"):
        return False
    if metadata.get("ignored_reason"):
        return False
    if filing.source_name in SEARCH_PAGE_SOURCE_NAMES:
        return False
    if filing.local_path and any(marker in file_url for marker in SEARCH_PAGE_URL_MARKERS):
        return False
    if ("annual report" in title or "年度报告" in title) and any(marker in title for marker in NON_REPORT_ANNUAL_NOTICE_MARKERS):
        return False
    return filing.parse_status == "parsed"


def get_effective_filings(session: Session, company_id: int) -> list[Filing]:
    """Return only usable filings, excluding search pages and stub rows."""

    return [item for item in get_filings(session, company_id) if is_effective_filing(item)]


def get_filing_by_id(session: Session, filing_id: int) -> Filing | None:
    """Return a filing by primary key."""

    if not filing_tables_ready(session):
        return None
    try:
        return session.scalar(select(Filing).where(Filing.id == filing_id))
    except SQLAlchemyError:
        session.rollback()
        return None


def get_filing_by_file_url(session: Session, company_id: int, file_url: str) -> Filing | None:
    """Return a filing by remote file URL."""

    if not filing_tables_ready(session):
        logger.warning("filing_lookup_by_file_url_tables_missing", company_id=company_id, file_url=file_url)
        return None
    stmt = select(Filing).where(Filing.company_id == company_id, Filing.file_url == file_url)
    try:
        return session.scalar(stmt)
    except SQLAlchemyError as exc:
        logger.warning("filing_lookup_by_file_url_failed", company_id=company_id, file_url=file_url, error=str(exc))
        session.rollback()
        return None


def get_filing_by_checksum(session: Session, company_id: int, checksum: str) -> Filing | None:
    """Return a filing by checksum for one company."""

    if not filing_tables_ready(session):
        return None
    stmt = select(Filing).where(Filing.company_id == company_id, Filing.checksum == checksum)
    try:
        return session.scalar(stmt)
    except SQLAlchemyError:
        session.rollback()
        return None


def get_filing_chunks(session: Session, company_id: int) -> list[FilingChunk]:
    """Return all filing chunks for a company."""

    if not filing_tables_ready(session):
        return []
    stmt = select(FilingChunk).where(FilingChunk.company_id == company_id).order_by(FilingChunk.id.asc())
    try:
        return list(session.scalars(stmt).all())
    except SQLAlchemyError:
        session.rollback()
        return []


def get_filing_chunks_by_filing(session: Session, filing_id: int) -> list[FilingChunk]:
    """Return all filing chunks for one filing."""

    if not filing_tables_ready(session):
        return []
    stmt = select(FilingChunk).where(FilingChunk.filing_id == filing_id).order_by(FilingChunk.chunk_index.asc())
    try:
        return list(session.scalars(stmt).all())
    except SQLAlchemyError:
        session.rollback()
        return []


def delete_filing_chunks(session: Session, filing_id: int) -> None:
    """Delete all chunks for a filing."""

    if not filing_tables_ready(session):
        return
    try:
        session.execute(delete(FilingChunk).where(FilingChunk.filing_id == filing_id))
    except SQLAlchemyError:
        session.rollback()
