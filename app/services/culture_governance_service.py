"""Corporate culture and governance analysis service."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.repositories.company_repository import get_company_by_ticker
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service
from app.services.retrieval_service import RetrievalService

logger = get_logger(__name__)

MANAGEMENT_QUERIES = [
    "management team, board of directors, CEO, CFO, executive officers, governance structure",
    "management changes, tenure, key personnel stability, core management members",
]

COMPENSATION_QUERIES = [
    "executive compensation, stock awards, equity incentive plan, salary, management remuneration",
    "performance metrics, incentive structure, long-term incentive, performance evaluation",
]

GOVERNANCE_QUERIES = [
    "corporate governance, shareholder structure, capital allocation, dividend, buyback",
    "corporate culture, values, organizational style, mission statement, business philosophy",
]


class CultureGovernanceService:
    """Analyze corporate culture, management, compensation, and governance."""

    def __init__(
        self,
        retrieval_service: RetrievalService | None = None,
        llm_service: AliyunLLMService | None = None,
    ) -> None:
        self.retrieval_service = retrieval_service
        self.llm_service = llm_service or get_llm_service()

    def analyze(
        self,
        session: Session,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        filing_coverage: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        company = get_company_by_ticker(session, ticker)
        warnings: list[str] = []

        management_evidence = self._retrieve_topic(company, MANAGEMENT_QUERIES) if company else []
        compensation_evidence = self._retrieve_topic(company, COMPENSATION_QUERIES) if company else []
        governance_evidence = self._retrieve_topic(company, GOVERNANCE_QUERIES) if company else []

        if not any([management_evidence, compensation_evidence, governance_evidence]):
            warnings.append("CultureAnalyst: Insufficient management/governance evidence retrieved.")

        llm_result = self._synthesize(
            ticker, company_name, company_profile,
            management_evidence, compensation_evidence, governance_evidence,
            model=model,
        )

        if llm_result is None:
            return self._fallback(company_profile, warnings)

        llm_result["warnings"] = warnings
        return llm_result

    def _retrieve_topic(self, company: Any, queries: list[str], top_k: int = 4) -> list[dict[str, Any]]:
        if self.retrieval_service is None:
            self.retrieval_service = RetrievalService()
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for query in queries:
            for hit in self.retrieval_service.search(
                query=query,
                filters={"company_id": company.id, "filing_type": "annual_report"},
                top_k=top_k,
            ):
                hit_id = f"{hit.get('filing_id')}:{hit.get('section')}"
                if hit_id not in seen_ids:
                    seen_ids.add(hit_id)
                    results.append(hit)
        return results[:8]

    def _synthesize(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        management_evidence: list[dict[str, Any]],
        compensation_evidence: list[dict[str, Any]],
        governance_evidence: list[dict[str, Any]],
        model: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.llm_service.available:
            return None
        try:
            from app.services.llm_prompt_templates import COMMON_JSON_RULES
            import json

            system_prompt = f"""You are a prudent equity research analyst specializing in corporate culture and governance analysis.
Your task is to analyze the following dimensions based on the provided company profile and annual report evidence:
1. Core management members and their backgrounds
2. Management stability signals
3. Compensation and equity incentive mechanisms
4. Corporate governance and capital allocation tendencies
5. Corporate culture characteristics

Analysis principles:
- Base your analysis only on the given evidence; do not fabricate names or facts
- If evidence is insufficient, explicitly mark the field as "Insufficient information"
- Management information must come from the input evidence; do not guess

{COMMON_JSON_RULES}"""

            user_payload = {
                "company_name": company_name,
                "ticker": ticker,
                "existing_profile": {
                    "corporate_culture": company_profile.get("corporate_culture", ""),
                    "management_summary": company_profile.get("management_summary", ""),
                },
                "management_evidence": [{"text": e.get("text", "")[:500], "section": e.get("section"), "filing_id": e.get("filing_id")} for e in management_evidence[:6]],
                "compensation_evidence": [{"text": e.get("text", "")[:500], "section": e.get("section")} for e in compensation_evidence[:4]],
                "governance_evidence": [{"text": e.get("text", "")[:500], "section": e.get("section")} for e in governance_evidence[:4]],
                "task": """Please output JSON:
{
  "core_judgement": "",
  "management_members": [
    {"name": "", "title": "", "background_summary": "", "stability_signal": ""}
  ],
  "management_stability_view": "",
  "compensation_and_incentive_view": "",
  "governance_style": "",
  "capital_allocation_tendency": "",
  "corporate_culture_view": "",
  "supporting_evidence_refs": [],
  "open_questions": []
}

Field descriptions:
- stability_signal: "stable" / "new" / "frequent_turnover" / "unknown"
- If evidence is insufficient for a field, fill in "Insufficient information"
""",
            }

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
            result = self.llm_service.chat_json(messages, model=model)
            if isinstance(result, dict):
                return result
            return None
        except Exception as exc:
            logger.warning("culture_analysis_llm_failed", ticker=ticker, error=str(exc))
            return None

    def _fallback(self, company_profile: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
        warnings.append("CultureAnalyst: LLM unavailable, falling back to basic profile.")
        return {
            "core_judgement": company_profile.get("corporate_culture", "Insufficient information"),
            "management_members": [],
            "management_stability_view": company_profile.get("management_summary", "Insufficient information"),
            "compensation_and_incentive_view": "Insufficient information",
            "governance_style": "Insufficient information",
            "capital_allocation_tendency": "Insufficient information",
            "corporate_culture_view": company_profile.get("corporate_culture", "Insufficient information"),
            "supporting_evidence_refs": [],
            "open_questions": ["Corporate culture and governance analysis relies on LLM deep synthesis; currently showing basic information only."],
            "warnings": warnings,
        }
