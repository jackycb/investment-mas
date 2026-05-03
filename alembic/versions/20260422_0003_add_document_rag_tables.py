"""add company document rag tables"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260422_0003"
down_revision = "20260419_0002"
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def upgrade() -> None:
    json_type = _json_type()

    op.add_column("company_profiles", sa.Column("risk_factors", json_type))

    op.create_table(
        "company_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("doc_type", sa.String(length=64), nullable=False),
        sa.Column("fiscal_year", sa.Integer()),
        sa.Column("report_date", sa.Date()),
        sa.Column("source_name", sa.String(length=128)),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=128)),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("raw_metadata", json_type),
        sa.UniqueConstraint("company_id", "source_url", name="uq_company_document_source"),
    )
    op.create_index("ix_company_documents_company_id", "company_documents", ["company_id"])
    op.create_index("ix_company_documents_ticker", "company_documents", ["ticker"])
    op.create_index("ix_company_documents_doc_type", "company_documents", ["doc_type"])
    op.create_index("ix_company_documents_fiscal_year", "company_documents", ["fiscal_year"])
    op.create_index("ix_company_documents_checksum", "company_documents", ["checksum"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_document_id", sa.Integer(), sa.ForeignKey("company_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("doc_type", sa.String(length=64), nullable=False),
        sa.Column("fiscal_year", sa.Integer()),
        sa.Column("section", sa.String(length=255)),
        sa.Column("page_number", sa.Integer()),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("chunk_metadata", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_document_id", "chunk_index", name="uq_document_chunk_index"),
    )
    op.create_index("ix_document_chunks_company_document_id", "document_chunks", ["company_document_id"])
    op.create_index("ix_document_chunks_company_id", "document_chunks", ["company_id"])
    op.create_index("ix_document_chunks_ticker", "document_chunks", ["ticker"])
    op.create_index("ix_document_chunks_doc_type", "document_chunks", ["doc_type"])
    op.create_index("ix_document_chunks_fiscal_year", "document_chunks", ["fiscal_year"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_fiscal_year", table_name="document_chunks")
    op.drop_index("ix_document_chunks_doc_type", table_name="document_chunks")
    op.drop_index("ix_document_chunks_ticker", table_name="document_chunks")
    op.drop_index("ix_document_chunks_company_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_company_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index("ix_company_documents_checksum", table_name="company_documents")
    op.drop_index("ix_company_documents_fiscal_year", table_name="company_documents")
    op.drop_index("ix_company_documents_doc_type", table_name="company_documents")
    op.drop_index("ix_company_documents_ticker", table_name="company_documents")
    op.drop_index("ix_company_documents_company_id", table_name="company_documents")
    op.drop_table("company_documents")

    op.drop_column("company_profiles", "risk_factors")
