"""Add node_executions telemetry table.

Stores per-span timing, status, retry count, cache-hit flag, LLM call count
and token usage for each research run. Purely additive — no existing table or
column is altered, so `alembic downgrade` restores the previous schema exactly.

Revision ID: 20261017_0006
Revises: 20260426_0005
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261017_0006"
down_revision = "20260426_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False, index=True),
        sa.Column("company_id", sa.Integer(), index=True),
        sa.Column("ticker", sa.String(32), index=True),
        sa.Column("node_name", sa.String(128), nullable=False, index=True),
        sa.Column("skill_name", sa.String(128), index=True),
        sa.Column("span_kind", sa.String(16), nullable=False, server_default="node"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Float()),
        sa.Column("status", sa.String(16), nullable=False, server_default="succeeded", index=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("llm_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_usage", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
        sa.Column("error_type", sa.String(128)),
        sa.Column("detail", sa.Text()),
    )
    op.create_index("ix_node_executions_task_started", "node_executions", ["task_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_node_executions_task_started", table_name="node_executions")
    op.drop_table("node_executions")
