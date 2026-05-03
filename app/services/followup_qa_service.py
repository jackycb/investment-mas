"""Follow-up QA service for post-report questions."""

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

QUERY_TYPES = [
    "report_followup",
    "section_deep_dive",
    "evidence_trace",
    "financial_question",
    "valuation_question",
    "news_update",
    "latest_info",
]


class FollowupQAService:
    """Answer follow-up questions using report context, filing evidence, and structured data."""

    def __init__(
        self,
        llm_service: AliyunLLMService | None = None,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self.llm_service = llm_service or get_llm_service()
        self.retrieval_service = retrieval_service

    def answer(
        self,
        session: Session,
        ticker: str,
        question: str,
        session_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Classify, plan retrieval, retrieve evidence, and compose answer."""

        report = get_latest_report_by_ticker(session, ticker)
        report_json = report.report_json if report else {}

        company = get_company_by_ticker(session, ticker)
        evidence = self._retrieve_evidence(company, question) if company else []

        answer, confidence = self._compose_answer(ticker, question, report_json, evidence)

        return {
            "answer": answer,
            "evidence_refs": [
                {
                    "source_type": "filing_chunk",
                    "filing_id": e.get("filing_id"),
                    "section": e.get("section"),
                    "page_number": e.get("page_number"),
                    "snippet": str(e.get("text", ""))[:200],
                    "score": e.get("score"),
                }
                for e in evidence[:5]
            ],
            "answer_confidence": confidence,
            "unresolved_questions": [],
        }

    def _retrieve_evidence(self, company: Any, question: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self.retrieval_service is None:
            self.retrieval_service = RetrievalService()
        return self.retrieval_service.search(
            query=question,
            filters={"company_id": company.id, "filing_type": "annual_report"},
            top_k=top_k,
        )

    def _compose_answer(
        self,
        ticker: str,
        question: str,
        report_json: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> tuple[str, float]:
        if not self.llm_service.available:
            return self._fallback_answer(report_json), 0.3

        try:
            evidence_texts = [
                f"[Source: {e.get('section', 'unknown')}] {str(e.get('text', ''))[:300]}"
                for e in evidence[:5]
            ]

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an investment research assistant. Answer user questions based on the given research report and annual report evidence.\n"
                        "Structure your answer as: conclusion first, then supporting evidence, then uncertainties.\n"
                        'If neither the report nor the evidence is sufficient to answer, clearly state "insufficient information".\n'
                        "Do not fabricate facts that are not provided."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "ticker": ticker,
                            "question": question,
                            "report_summary": {
                                "executive_summary": report_json.get("executive_summary", ""),
                                "final_rating": report_json.get("final_rating", ""),
                            },
                            "filing_evidence": evidence_texts,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            content = self.llm_service.chat(messages, temperature=0.1)
            return (content or "").strip() or self._fallback_answer(report_json), 0.7
        except Exception as exc:
            logger.warning("followup_qa_llm_failed", ticker=ticker, error=str(exc))
            return self._fallback_answer(report_json), 0.3

    def _fallback_answer(self, report_json: dict[str, Any]) -> str:
        summary = str(report_json.get("executive_summary", ""))
        if summary:
            return summary
        return "Insufficient report information to answer this question."
