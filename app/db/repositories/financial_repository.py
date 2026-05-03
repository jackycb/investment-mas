"""Repository functions for financial data."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FinancialReport


def get_annual_reports(session: Session, company_id: int, limit: int = 5) -> list[FinancialReport]:
    """Fetch recent annual reports ordered newest first."""

    stmt = (
        select(FinancialReport)
        .where(FinancialReport.company_id == company_id, FinancialReport.period_type == "annual")
        .order_by(FinancialReport.fiscal_year.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())

