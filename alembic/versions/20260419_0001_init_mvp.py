"""initial mvp schema"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260419_0001"
down_revision = None
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB(astext_type=sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def upgrade() -> None:
    json_type = _json_type()

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=64)),
        sa.Column("market", sa.String(length=64)),
        sa.Column("currency", sa.String(length=16)),
        sa.Column("country", sa.String(length=64)),
        sa.Column("sector", sa.String(length=128)),
        sa.Column("industry", sa.String(length=128)),
        sa.Column("sub_industry", sa.String(length=128)),
        sa.Column("official_website", sa.String(length=255)),
        sa.Column("ir_website", sa.String(length=255)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_companies_ticker", "companies", ["ticker"], unique=True)

    op.create_table(
        "company_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("business_model", sa.Text()),
        sa.Column("market_space", sa.Text()),
        sa.Column("competition_landscape", sa.Text()),
        sa.Column("corporate_culture", sa.Text()),
        sa.Column("revenue_model", sa.Text()),
        sa.Column("key_products", json_type),
        sa.Column("key_regions", json_type),
        sa.Column("key_customers", json_type),
        sa.Column("key_suppliers", json_type),
        sa.Column("management_summary", sa.Text()),
        sa.Column("raw_sources", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "peer_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("peer_company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("peer_type", sa.String(length=32), nullable=False),
        sa.Column("similarity_score", sa.Float()),
        sa.Column("rationale", sa.Text()),
        sa.Column("source_type", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_id", "peer_company_id", "peer_type", name="uq_peer_mapping"),
    )

    op.create_table(
        "financial_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=16), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("revenue", sa.Numeric(20, 4)),
        sa.Column("gross_profit", sa.Numeric(20, 4)),
        sa.Column("operating_profit", sa.Numeric(20, 4)),
        sa.Column("net_income", sa.Numeric(20, 4)),
        sa.Column("operating_cash_flow", sa.Numeric(20, 4)),
        sa.Column("free_cash_flow", sa.Numeric(20, 4)),
        sa.Column("total_assets", sa.Numeric(20, 4)),
        sa.Column("total_liabilities", sa.Numeric(20, 4)),
        sa.Column("total_equity", sa.Numeric(20, 4)),
        sa.Column("shares_outstanding", sa.Numeric(20, 4)),
        sa.Column("gross_margin", sa.Float()),
        sa.Column("net_margin", sa.Float()),
        sa.Column("roe", sa.Float()),
        sa.Column("roa", sa.Float()),
        sa.Column("debt_ratio", sa.Float()),
        sa.Column("raw_source", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "financial_peer_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("company_value", sa.Float()),
        sa.Column("peer_avg_value", sa.Float()),
        sa.Column("peer_median_value", sa.Float()),
        sa.Column("ranking", sa.Integer()),
        sa.Column("percentile", sa.Float()),
        sa.Column("details", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "news_articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_name", sa.String(length=128)),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=500)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("author", sa.String(length=128)),
        sa.Column("content", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("language", sa.String(length=32)),
        sa.Column("raw_html", sa.Text()),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "news_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("news_articles.id", ondelete="SET NULL")),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_date", sa.Date()),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("sentiment", sa.String(length=32)),
        sa.Column("importance_score", sa.Float()),
        sa.Column("confidence_score", sa.Float()),
        sa.Column("risk_tags", json_type),
        sa.Column("entities", json_type),
        sa.Column("event_summary", sa.Text()),
        sa.Column("impact_dimensions", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_date", sa.Date(), nullable=False),
        sa.Column("fundamentals_risk_score", sa.Float()),
        sa.Column("competition_risk_score", sa.Float()),
        sa.Column("governance_risk_score", sa.Float()),
        sa.Column("event_risk_score", sa.Float()),
        sa.Column("valuation_risk_score", sa.Float()),
        sa.Column("overall_risk_score", sa.Float()),
        sa.Column("red_flags", json_type),
        sa.Column("key_risks", json_type),
        sa.Column("monitoring_signals", json_type),
        sa.Column("recommendation", sa.Text()),
        sa.Column("supporting_evidence", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "valuation_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("valuation_method", sa.String(length=64), nullable=False),
        sa.Column("bull_value", sa.Numeric(20, 4)),
        sa.Column("base_value", sa.Numeric(20, 4)),
        sa.Column("bear_value", sa.Numeric(20, 4)),
        sa.Column("assumptions", json_type),
        sa.Column("sensitivity", json_type),
        sa.Column("conclusion", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "research_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("executive_summary", sa.Text()),
        sa.Column("report_markdown", sa.Text(), nullable=False),
        sa.Column("report_json", json_type),
        sa.Column("final_rating", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    for table_name in [
        "research_reports",
        "valuation_results",
        "risk_assessments",
        "news_events",
        "news_articles",
        "financial_peer_snapshots",
        "financial_reports",
        "peer_mappings",
        "company_profiles",
        "companies",
    ]:
        op.drop_table(table_name)

