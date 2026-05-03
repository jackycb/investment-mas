"""Feedback-driven section refinement service."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.repositories.company_repository import get_company_by_ticker
from app.db.repositories.research_repository import get_latest_report_by_ticker
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service
from app.services.retrieval_service import RetrievalService

logger = get_logger(__name__)

SECTION_MAP = {
    "business_analysis": "Business Analysis",
    "segment_analysis": "Segment Analysis",
    "culture_governance": "Culture & Governance",
    "financial_analysis": "Financial Analysis",
    "peer_comparison": "Peer Comparison",
    "valuation": "Valuation Analysis",
    "risk_review": "Risk Review",
}

FEEDBACK_TYPES = ["too_shallow", "incomplete", "inaccurate", "need_more_detail", "missing_evidence", "other"]


class FeedbackRefinementService:
    """Refine specific report sections based on user feedback."""

    def __init__(
        self,
        llm_service: AliyunLLMService | None = None,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self.llm_service = llm_service or get_llm_service()
        self.retrieval_service = retrieval_service

    def refine(
        self,
        session: Session,
        ticker: str,
        section_name: str,
        feedback_type: str,
        feedback_text: str,
    ) -> dict[str, Any]:
        """Refine a specific section based on user feedback."""

        report = get_latest_report_by_ticker(session, ticker)
        report_json = report.report_json if report else {}
        sections = report_json.get("sections", {})

        impacted = self._detect_impacted_sections(section_name, feedback_type)
        company = get_company_by_ticker(session, ticker)
        additional_evidence = self._retrieve_additional_evidence(company, section_name, feedback_text) if company else []

        refined = self._regenerate_section(
            ticker, section_name, feedback_type, feedback_text,
            sections, additional_evidence,
        )

        return {
            "updated_section": section_name,
            "impacted_sections": impacted,
            "refined_content": refined,
            "additional_evidence_count": len(additional_evidence),
            "feedback_resolution": f"The {SECTION_MAP.get(section_name, section_name)} section has been regenerated based on feedback.",
        }

    def _detect_impacted_sections(self, section_name: str, feedback_type: str) -> list[str]:
        """Determine which other sections might be affected by this feedback."""

        impacts: dict[str, list[str]] = {
            "business_analysis": ["risk_review", "valuation"],
            "segment_analysis": ["business_analysis", "financial_analysis"],
            "culture_governance": ["risk_review"],
            "financial_analysis": ["valuation", "risk_review"],
            "valuation": ["risk_review"],
            "risk_review": [],
            "peer_comparison": ["valuation"],
        }
        return impacts.get(section_name, [])

    def _retrieve_additional_evidence(self, company: Any, section_name: str, feedback_text: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self.retrieval_service is None:
            self.retrieval_service = RetrievalService()

        query = f"Retrieve annual report evidence related to the following feedback: {feedback_text}"
        return self.retrieval_service.search(
            query=query,
            filters={"company_id": company.id, "filing_type": "annual_report"},
            top_k=top_k,
        )

    def _regenerate_section(
        self,
        ticker: str,
        section_name: str,
        feedback_type: str,
        feedback_text: str,
        sections: dict[str, Any],
        additional_evidence: list[dict[str, Any]],
    ) -> str:
        if not self.llm_service.available:
            return f"LLM unavailable, unable to regenerate {SECTION_MAP.get(section_name, section_name)}."

        try:
            section_label = SECTION_MAP.get(section_name, section_name)
            original_content = str(sections.get(section_name, sections.get(section_label, "")))
            evidence_texts = [
                f"[{e.get('section', '')}] {str(e.get('text', ''))[:300]}"
                for e in additional_evidence[:5]
            ]

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an investment research analyst. The user is dissatisfied with a section of the report. "
                        "Please regenerate that section based on the original content, user feedback, and supplementary evidence.\n"
                        "Respond in English. Maintain a professional and prudent tone.\n"
                        "Do not fabricate facts that have not been provided."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "ticker": ticker,
                            "section": section_label,
                            "feedback_type": feedback_type,
                            "feedback": feedback_text,
                            "original_content": original_content[:1000],
                            "additional_evidence": evidence_texts,
                            "task": f"Please regenerate the {section_label} section to address the user's feedback.",
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            content = self.llm_service.chat(messages, temperature=0.15)
            return (content or "").strip() or original_content
        except Exception as exc:
            logger.warning("feedback_refinement_llm_failed", ticker=ticker, section=section_name, error=str(exc))
            return f"Regeneration failed: {exc}"
