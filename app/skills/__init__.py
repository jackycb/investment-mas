"""Reusable capability modules (skills) for the research workflow.

A skill wraps an existing Service with an explicit input/output contract,
bounded error handling, measurable runtime and result caching. Skills never
re-implement a formula that already exists in a Service, and never add a
pass-through abstraction layer (optimization task §4.5 / §11).

  * :mod:`app.skills.financial_metrics`          — deterministic indicators
  * :mod:`app.skills.valuation`                  — wraps the legacy Python
    valuation program unchanged (buy/sell price formulas untouched)
  * :mod:`app.skills.financial_report_ingestion` — filing download/parse/index
  * :mod:`app.skills.evidence_retrieval`         — unified evidence lookup
  * :mod:`app.skills.market_data`                — cached peer market metrics
"""

from __future__ import annotations

from app.skills.base import (
    PermanentSkillError,
    SkillError,
    SkillResult,
    SkillUnavailableError,
    TransientSkillError,
    classify_error,
    retry_transient,
    skill_span,
)

__all__ = [
    "PermanentSkillError",
    "SkillError",
    "SkillResult",
    "SkillUnavailableError",
    "TransientSkillError",
    "classify_error",
    "retry_transient",
    "skill_span",
]
