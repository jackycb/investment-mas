"""Add enhanced analysis tables: segments, management, incentives, evidence, sessions, followup, feedback.

Revision ID: 20260426_0005
Revises: 20260422_0004
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260426_0005"
down_revision = "20260422_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("session_type", sa.String(32), nullable=False, server_default="research"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("report_json", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("report_markdown", sa.Text()),
        sa.Column("final_rating", sa.String(32)),
        sa.Column("summary", sa.Text()),
        sa.Column("warnings", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("coverage_result", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "company_segment_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("report_session_id", sa.Integer(), sa.ForeignKey("report_sessions.id", ondelete="SET NULL"), index=True),
        sa.Column("segment_name", sa.String(255), nullable=False),
        sa.Column("revenue_share", sa.Float()),
        sa.Column("profit_share", sa.Float()),
        sa.Column("business_description", sa.Text()),
        sa.Column("market_context", sa.Text()),
        sa.Column("competition_context", sa.Text()),
        sa.Column("key_drivers", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("key_risks", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("evidence_refs", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "management_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255)),
        sa.Column("tenure_years", sa.Float()),
        sa.Column("joined_date", sa.String(32)),
        sa.Column("background_summary", sa.Text()),
        sa.Column("stability_signal", sa.String(64)),
        sa.Column("evidence_refs", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "incentive_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("fiscal_year", sa.Integer()),
        sa.Column("incentive_type", sa.String(64), nullable=False),
        sa.Column("target_group", sa.String(128)),
        sa.Column("summary", sa.Text()),
        sa.Column("amount_or_ratio", sa.String(128)),
        sa.Column("evidence_refs", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "evidence_spans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(64), nullable=False, index=True),
        sa.Column("entity_id", sa.Integer(), nullable=False, index=True),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.String(255)),
        sa.Column("chunk_id", sa.String(128)),
        sa.Column("page_number", sa.Integer()),
        sa.Column("section", sa.String(255)),
        sa.Column("snippet", sa.Text()),
        sa.Column("score", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "followup_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_session_id", sa.Integer(), sa.ForeignKey("report_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("evidence_refs", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("answer_confidence", sa.Float()),
        sa.Column("unresolved_questions", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_session_id", sa.Integer(), sa.ForeignKey("report_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("section_name", sa.String(128)),
        sa.Column("feedback_type", sa.String(64), nullable=False),
        sa.Column("feedback_text", sa.Text()),
        sa.Column("action_taken", sa.String(64)),
        sa.Column("patch_result", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_feedback")
    op.drop_table("followup_questions")
    op.drop_table("evidence_spans")
    op.drop_table("incentive_records")
    op.drop_table("management_profiles")
    op.drop_table("company_segment_profiles")
    op.drop_table("report_sessions")
