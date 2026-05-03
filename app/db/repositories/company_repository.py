"""Repository functions for company-centric persistence."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Company, CompanyProfile, PeerMapping
from app.utils.metadata_utils import normalize_ticker


def get_company_by_ticker(session: Session, ticker: str) -> Company | None:
    """Fetch a company by ticker."""

    return session.scalar(select(Company).where(Company.ticker == normalize_ticker(ticker)))


def get_or_create_company(session: Session, ticker: str, company_name: str) -> Company:
    """Fetch or create a company row without poisoning the session on duplicate ticker races."""

    normalized_ticker = normalize_ticker(ticker)
    if not session.is_active:
        session.rollback()

    with session.no_autoflush:
        company = get_company_by_ticker(session, normalized_ticker)
    now = datetime.now(timezone.utc)
    if company:
        if company.company_name != company_name:
            company.company_name = company_name
        company.updated_at = now
        return company

    try:
        with session.begin_nested():
            company = Company(ticker=normalized_ticker, company_name=company_name, created_at=now, updated_at=now)
            session.add(company)
            session.flush()
        return company
    except IntegrityError:
        if not session.is_active:
            session.rollback()
        with session.no_autoflush:
            company = get_company_by_ticker(session, normalized_ticker)
        if company is None:
            raise
        if company.company_name != company_name:
            company.company_name = company_name
        company.updated_at = now
        return company


def get_latest_company_profile(session: Session, company_id: int) -> CompanyProfile | None:
    """Fetch the latest profile row for a company."""

    stmt = select(CompanyProfile).where(CompanyProfile.company_id == company_id).order_by(CompanyProfile.as_of_date.desc())
    return session.scalar(stmt)


def get_peer_mappings(session: Session, company_id: int) -> list[PeerMapping]:
    """Fetch peer mappings for a company."""

    stmt = select(PeerMapping).where(PeerMapping.company_id == company_id)
    return list(session.scalars(stmt).all())


def get_company_by_name(session: Session, company_name: str) -> Company | None:
    """Fetch a company by exact company name."""

    return session.scalar(select(Company).where(Company.company_name == company_name))
