"""Repository functions for persisted research outputs."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Company, ResearchReport
from app.utils.metadata_utils import normalize_ticker


def get_latest_report_by_ticker(session: Session, ticker: str) -> ResearchReport | None:
    """Fetch the latest research report for a ticker."""

    stmt = (
        select(ResearchReport)
        .join(Company, Company.id == ResearchReport.company_id)
        .where(Company.ticker == normalize_ticker(ticker))
        .order_by(ResearchReport.report_date.desc(), ResearchReport.id.desc())
    )
    return session.scalar(stmt)

