"""add filing knowledge layer tables"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260422_0004"
down_revision = "20260422_0003"
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def upgrade() -> None:
    json_type = _json_type()

    op.create_table(
        "filings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("filing_type", sa.String(length=64), nullable=False),
        sa.Column("period_type", sa.String(length=32)),
        sa.Column("fiscal_year", sa.Integer()),
        sa.Column("fiscal_period", sa.String(length=32)),
        sa.Column("report_date", sa.Date()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("title", sa.String(length=500)),
        sa.Column("source_name", sa.String(length=128)),
        sa.Column("source_url", sa.Text()),
        sa.Column("file_url", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text()),
        sa.Column("file_type", sa.String(length=32)),
        sa.Column("checksum", sa.String(length=128)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("mime_type", sa.String(length=128)),
        sa.Column("download_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("parse_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("metadata_json", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_id", "file_url", name="uq_filing_file_url"),
    )
    op.create_index("ix_filings_company_id", "filings", ["company_id"])
    op.create_index("ix_filings_ticker", "filings", ["ticker"])
    op.create_index("ix_filings_market", "filings", ["market"])
    op.create_index("ix_filings_filing_type", "filings", ["filing_type"])
    op.create_index("ix_filings_fiscal_year", "filings", ["fiscal_year"])
    op.create_index("ix_filings_checksum", "filings", ["checksum"])

    op.create_table(
        "filing_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filing_id", sa.Integer(), sa.ForeignKey("filings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=255)),
        sa.Column("page_number", sa.Integer()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer()),
        sa.Column("embedding_model", sa.String(length=128)),
        sa.Column("embedding_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("metadata_json", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("filing_id", "chunk_index", name="uq_filing_chunk_index"),
    )
    op.create_index("ix_filing_chunks_filing_id", "filing_chunks", ["filing_id"])
    op.create_index("ix_filing_chunks_company_id", "filing_chunks", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_filing_chunks_company_id", table_name="filing_chunks")
    op.drop_index("ix_filing_chunks_filing_id", table_name="filing_chunks")
    op.drop_table("filing_chunks")

    op.drop_index("ix_filings_checksum", table_name="filings")
    op.drop_index("ix_filings_fiscal_year", table_name="filings")
    op.drop_index("ix_filings_filing_type", table_name="filings")
    op.drop_index("ix_filings_market", table_name="filings")
    op.drop_index("ix_filings_ticker", table_name="filings")
    op.drop_index("ix_filings_company_id", table_name="filings")
    op.drop_table("filings")
