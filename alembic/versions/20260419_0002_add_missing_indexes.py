"""add missing indexes for foreign keys and query fields"""

from alembic import op

revision = "20260419_0002"
down_revision = "20260419_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_company_profiles_company_id", "company_profiles", ["company_id"])
    op.create_index("ix_peer_mappings_company_id", "peer_mappings", ["company_id"])
    op.create_index("ix_peer_mappings_peer_company_id", "peer_mappings", ["peer_company_id"])
    op.create_index("ix_financial_reports_company_id", "financial_reports", ["company_id"])
    op.create_index("ix_financial_reports_fiscal_year", "financial_reports", ["fiscal_year"])
    op.create_index("ix_financial_peer_snapshots_company_id", "financial_peer_snapshots", ["company_id"])
    op.create_index("ix_news_articles_company_id", "news_articles", ["company_id"])
    op.create_index("ix_news_events_article_id", "news_events", ["article_id"])
    op.create_index("ix_news_events_company_id", "news_events", ["company_id"])
    op.create_index("ix_risk_assessments_company_id", "risk_assessments", ["company_id"])
    op.create_index("ix_valuation_results_company_id", "valuation_results", ["company_id"])
    op.create_index("ix_research_reports_company_id", "research_reports", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_research_reports_company_id", table_name="research_reports")
    op.drop_index("ix_valuation_results_company_id", table_name="valuation_results")
    op.drop_index("ix_risk_assessments_company_id", table_name="risk_assessments")
    op.drop_index("ix_news_events_company_id", table_name="news_events")
    op.drop_index("ix_news_events_article_id", table_name="news_events")
    op.drop_index("ix_news_articles_company_id", table_name="news_articles")
    op.drop_index("ix_financial_peer_snapshots_company_id", table_name="financial_peer_snapshots")
    op.drop_index("ix_financial_reports_fiscal_year", table_name="financial_reports")
    op.drop_index("ix_financial_reports_company_id", table_name="financial_reports")
    op.drop_index("ix_peer_mappings_peer_company_id", table_name="peer_mappings")
    op.drop_index("ix_peer_mappings_company_id", table_name="peer_mappings")
    op.drop_index("ix_company_profiles_company_id", table_name="company_profiles")

