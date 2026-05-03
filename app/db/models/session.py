"""Report session, followup, and feedback ORM models."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class ReportSession(Base):
    """A complete research session encompassing report generation and followups."""

    __tablename__ = "report_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False)
    session_type: Mapped[str] = mapped_column(String(32), nullable=False, default="research")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    report_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    report_markdown: Mapped[str | None] = mapped_column(Text)
    final_rating: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str] | None] = mapped_column(JSONType)
    coverage_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    company: Mapped["Company"] = relationship()


class FollowupQuestion(Base):
    """Persisted follow-up questions and answers after report generation."""

    __tablename__ = "followup_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_session_id: Mapped[int] = mapped_column(ForeignKey("report_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)
    answer_confidence: Mapped[float | None] = mapped_column(Float)
    unresolved_questions: Mapped[list[str] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["ReportSession"] = relationship()


class UserFeedback(Base):
    """Persisted user feedback for section-level refinement."""

    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_session_id: Mapped[int] = mapped_column(ForeignKey("report_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    section_name: Mapped[str | None] = mapped_column(String(128))
    feedback_type: Mapped[str] = mapped_column(String(64), nullable=False)
    feedback_text: Mapped[str | None] = mapped_column(Text)
    action_taken: Mapped[str | None] = mapped_column(String(64))
    patch_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["ReportSession"] = relationship()
