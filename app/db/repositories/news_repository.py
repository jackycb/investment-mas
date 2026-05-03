"""Repository functions for news data."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import NewsArticle


def get_recent_articles(session: Session, company_id: int, limit: int = 10) -> list[NewsArticle]:
    """Fetch recent articles for a company."""

    stmt = select(NewsArticle).where(NewsArticle.company_id == company_id).order_by(NewsArticle.published_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())

