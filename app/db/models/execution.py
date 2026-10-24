"""ORM model for lightweight workflow execution telemetry.

One row per node / skill / LLM span inside a research run. Used to answer
the performance questions in the optimization task (total runtime, slowest
nodes, real parallelism, cache hit rate, LLM call count, token usage).

The table is additive: nothing else in the schema depends on it, so a
missing table only disables telemetry, never the research workflow.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class NodeExecution(Base):
    """Persisted timing / status record for a single execution span."""

    __tablename__ = "node_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Research run this span belongs to (UUID string minted per run).
    task_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    # Optional company linkage. company_id stays nullable because spans can be
    # recorded before the Company row is resolved (e.g. ticker normalization).
    company_id: Mapped[int | None] = mapped_column(Integer, index=True)
    ticker: Mapped[str | None] = mapped_column(String(32), index=True)

    # Span identity. node_name is the workflow step; skill_name is set when the
    # span measures a reusable skill rather than a graph node.
    node_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    skill_name: Mapped[str | None] = mapped_column(String(128), index=True)
    span_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="node")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)

    # pending | running | succeeded | failed | retrying | skipped
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="succeeded", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    llm_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error_type: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[str | None] = mapped_column(Text)
