"""Repository helpers for company documents and chunks."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import CompanyDocument, DocumentChunk


def get_company_documents(session: Session, company_id: int) -> list[CompanyDocument]:
    """Return company documents ordered by latest report date first."""

    stmt = (
        select(CompanyDocument)
        .where(CompanyDocument.company_id == company_id)
        .order_by(CompanyDocument.report_date.desc().nullslast(), CompanyDocument.downloaded_at.desc())
    )
    return list(session.scalars(stmt).all())


def get_company_document_by_id(session: Session, document_id: int) -> CompanyDocument | None:
    """Fetch one company document by primary key."""

    return session.scalar(select(CompanyDocument).where(CompanyDocument.id == document_id))


def get_document_by_source_url(session: Session, company_id: int, source_url: str) -> CompanyDocument | None:
    """Fetch a single company document by source URL."""

    stmt = select(CompanyDocument).where(
        CompanyDocument.company_id == company_id,
        CompanyDocument.source_url == source_url,
    )
    return session.scalar(stmt)


def get_document_chunks(session: Session, company_id: int) -> list[DocumentChunk]:
    """Return all chunks for a company."""

    stmt = select(DocumentChunk).where(DocumentChunk.company_id == company_id).order_by(DocumentChunk.id.asc())
    return list(session.scalars(stmt).all())


def get_document_chunks_by_document(session: Session, company_document_id: int) -> list[DocumentChunk]:
    """Return all chunks for a single company document."""

    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.company_document_id == company_document_id)
        .order_by(DocumentChunk.chunk_index.asc())
    )
    return list(session.scalars(stmt).all())


def delete_document_chunks(session: Session, company_document_id: int) -> None:
    """Delete chunks for a single company document."""

    session.execute(delete(DocumentChunk).where(DocumentChunk.company_document_id == company_document_id))
