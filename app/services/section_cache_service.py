"""Section-level caching for research workflow outputs."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Company, ResearchReport

logger = get_logger(__name__)

CACHEABLE_SECTIONS = {
    "company_profile",
    "business_analysis",
    "segment_analysis",
    "culture_analysis",
    "peer_analysis",
    "financial_summary",
    "peer_comparison",
    "valuation_result",
    "risk_assessment",
}

MAX_CACHE_AGE_DAYS = 7


class SectionCacheService:
    """Read and write per-section caches backed by the latest ResearchReport."""

    def load_cached_state(self, session: Session, company: Company) -> dict[str, Any]:
        """Load the most recent report JSON and return section-level cached data."""

        report = self._latest_report(session, company.id)
        if report is None:
            return {}
        report_json = report.report_json or {}
        created = report.created_at
        now_utc = datetime.now(timezone.utc)
        created_aware = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
        if created and (now_utc - created_aware).days > MAX_CACHE_AGE_DAYS:
            logger.info("section_cache_expired", ticker=company.ticker, age_days=(datetime.now(timezone.utc) - created).days)
            return {}

        cached: dict[str, Any] = {}
        sections_data = report_json.get("_section_cache", {})
        for section_key in CACHEABLE_SECTIONS:
            if section_key in sections_data:
                cached[section_key] = sections_data[section_key]
        if cached:
            logger.info("section_cache_loaded", ticker=company.ticker, sections=list(cached.keys()))
        return cached

    def save_section_cache(self, report_json: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Embed cacheable sections into report_json for next-run reuse."""

        cache_payload: dict[str, Any] = {}
        for section_key in CACHEABLE_SECTIONS:
            value = state.get(section_key)
            if value:
                cache_payload[section_key] = value
        if cache_payload:
            report_json["_section_cache"] = cache_payload
            report_json["_cache_date"] = date.today().isoformat()
        return report_json

    def _latest_report(self, session: Session, company_id: int) -> ResearchReport | None:
        return session.scalar(
            select(ResearchReport)
            .where(ResearchReport.company_id == company_id)
            .order_by(ResearchReport.created_at.desc())
            .limit(1)
        )


def compute_input_hash(*args: Any) -> str:
    """Compute a deterministic hash of inputs for cache invalidation."""

    payload = json.dumps(args, sort_keys=True, default=str)
    return hashlib.md5(payload.encode()).hexdigest()[:12]
