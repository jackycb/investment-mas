"""Business analysis service for market space, segments, and competition deep-dive."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import FilingChunk
from app.db.repositories.company_repository import get_company_by_ticker
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service
from app.services.retrieval_service import RetrievalService

logger = get_logger(__name__)

SEGMENT_QUERIES = [
    "business segments, segment revenue, segment profit, revenue breakdown 业务板块、分部收入、分部利润",
    "product line revenue, revenue mix, revenue by category 主营业务占比、各业务线收入",
]

MARKET_QUERIES = [
    "total addressable market, TAM, market size, market growth, industry drivers 市场规模、行业增长、市场空间",
    "industry trends, demand drivers, customer growth 行业趋势、需求驱动、客户增长",
]

COMPETITION_QUERIES = [
    "competitive landscape, competitors, market share, competitive advantages, barriers to entry 竞争格局、主要竞争对手、竞争壁垒",
]

SEGMENT_KEYWORDS = ["segment", "segments", "segment revenue", "revenue breakdown", "分部", "业务板块", "主营业务"]
MARKET_KEYWORDS = ["market", "industry", "demand", "growth", "customer", "市场", "行业", "需求", "增长"]
COMPETITION_KEYWORDS = ["competition", "competitive", "competitors", "market share", "竞争", "竞争对手", "市场份额"]


class BusinessAnalysisService:
    """Deep-dive business model, segment, market space, and competition analysis."""

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
        """Return business_analysis and segment_analysis dicts."""

        company = get_company_by_ticker(session, ticker)
        warnings: list[str] = []

        segment_evidence = self._retrieve_topic(session, company, SEGMENT_QUERIES, SEGMENT_KEYWORDS) if company else []
        market_evidence = self._retrieve_topic(session, company, MARKET_QUERIES, MARKET_KEYWORDS) if company else []
        competition_evidence = self._retrieve_topic(session, company, COMPETITION_QUERIES, COMPETITION_KEYWORDS) if company else []

        if not any([segment_evidence, market_evidence, competition_evidence]):
            warnings.append("BusinessAnalyst: Insufficient annual report evidence retrieved; business analysis will be based on company profile.")

        llm_result = self._synthesize(
            ticker, company_name, company_profile,
            segment_evidence, market_evidence, competition_evidence,
            filing_coverage, model=model,
        )

        if llm_result is None:
            return self._fallback(company_profile, segment_evidence, market_evidence, competition_evidence, warnings)
        if self._llm_result_is_insufficient(llm_result) and any([segment_evidence, market_evidence, competition_evidence]):
            logger.warning(
                "business_analysis_llm_insufficient_with_evidence",
                ticker=ticker,
                segment_evidence_count=len(segment_evidence),
                market_evidence_count=len(market_evidence),
                competition_evidence_count=len(competition_evidence),
            )
            warnings.append("BusinessAnalyst: LLM returned insufficient analysis despite annual report evidence; using local evidence fallback.")
            return self._fallback(company_profile, segment_evidence, market_evidence, competition_evidence, warnings)

        llm_result["warnings"] = warnings
        return llm_result

    def _llm_result_is_insufficient(self, result: dict[str, Any]) -> bool:
        """Detect empty LLM business analysis so local filing evidence can take over."""

        business = result.get("business_analysis")
        if not isinstance(business, dict):
            return True
        core = str(business.get("core_judgement") or "").strip()
        detail = str(business.get("business_model_detail") or "").strip()
        competition = business.get("competition")
        competition_core = ""
        if isinstance(competition, dict):
            competition_core = str(competition.get("core_judgement") or "").strip()
        meaningful = [value for value in (core, detail, competition_core) if value and not self._is_insufficient_text(value)]
        return not meaningful

    def _is_insufficient_text(self, value: Any) -> bool:
        """Return true for common empty-analysis phrases."""

        text = str(value or "").strip().lower()
        if not text:
            return True
        first_line = text.splitlines()[0].strip()
        markers = (
            "insufficient information",
            "information is insufficient",
            "unable to assess",
            "cannot assess",
            "not enough information",
            "信息不足",
            "无法判断",
        )
        return any(first_line.startswith(marker) or marker in first_line[:180] for marker in markers)

    def _retrieve_topic(
        self,
        session: Session,
        company: Any,
        queries: list[str],
        keywords: list[str],
        top_k: int = 4,
    ) -> list[dict[str, Any]]:
        """Retrieve business evidence from vector search, then local chunks as fallback."""

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
        if results:
            return results[:8]
        return self._keyword_fallback(session, company.id, keywords, top_k=top_k * 2)

    def _keyword_fallback(self, session: Session, company_id: int, keywords: list[str], top_k: int = 8) -> list[dict[str, Any]]:
        """Fallback SQL keyword search over annual report chunks when vector search is unavailable."""

        if not keywords:
            return []
        conditions = [func.lower(FilingChunk.text).contains(keyword.lower()) for keyword in keywords]
        stmt = (
            select(FilingChunk)
            .where(FilingChunk.company_id == company_id)
            .where(or_(*conditions))
            .limit(top_k)
        )
        rows = session.scalars(stmt).all()
        results: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.metadata_json or {}
            if metadata.get("filing_type") not in {None, "annual_report"}:
                continue
            score = sum(1 for keyword in keywords if keyword.lower() in f"{row.section or ''} {row.text}".lower())
            results.append(
                {
                    "id": f"{row.filing_id}:{row.chunk_index}",
                    "text": row.text,
                    "score": float(score or 1),
                    "filing_id": row.filing_id,
                    "section": row.section,
                    "page_number": row.page_number,
                    "metadata": metadata,
                }
            )
        return results

    def _synthesize(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        segment_evidence: list[dict[str, Any]],
        market_evidence: list[dict[str, Any]],
        competition_evidence: list[dict[str, Any]],
        filing_coverage: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.llm_service.available:
            return None
        try:
            from app.services.llm_prompt_templates import COMMON_JSON_RULES
            import json

            system_prompt = f"""You are a prudent equity research analyst specializing in deep business analysis.
Your task is to complete the following analyses based on the provided company profile and annual report evidence:
1. In-depth business model analysis
2. Market space and driver analysis
3. Business segment breakdown (if the company is a conglomerate/group)
4. Competitive landscape analysis

Analysis principles:
- Base your analysis only on the given evidence; do not fabricate facts
- If the company is a conglomerate/group, you must attempt to break down the major business segments
- If segment revenue/profit share data is missing, do not fabricate it, but indicate which segments have been identified and which lack quantitative data
- Provide supporting_evidence_refs for each judgement

{COMMON_JSON_RULES}"""

            user_payload = {
                "company_name": company_name,
                "ticker": ticker,
                "company_profile": {
                    k: company_profile.get(k)
                    for k in ("business_model", "revenue_model", "market_space", "competition_landscape", "key_products", "key_regions")
                },
                "segment_evidence": [{"text": e.get("text", "")[:500], "section": e.get("section"), "filing_id": e.get("filing_id")} for e in segment_evidence[:6]],
                "market_evidence": [{"text": e.get("text", "")[:500], "section": e.get("section")} for e in market_evidence[:4]],
                "competition_evidence": [{"text": e.get("text", "")[:500], "section": e.get("section")} for e in competition_evidence[:4]],
                "coverage_gaps": filing_coverage.get("missing", []),
                "task": """Please output JSON:
{
  "business_analysis": {
    "core_judgement": "",
    "business_model_detail": "",
    "market_space": {"core_judgement": "", "market_drivers": [], "market_size_estimate": "", "open_questions": []},
    "competition": {"core_judgement": "", "key_competitors": [], "competitive_advantages": [], "competitive_weaknesses": [], "open_questions": []},
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "segment_analysis": {
    "is_group_company": false,
    "segments": [
      {"segment_name": "", "revenue_share": null, "profit_share": null, "business_description": "", "market_context": "", "competition_context": "", "key_drivers": [], "key_risks": []}
    ],
    "segment_level_open_questions": [],
    "supporting_evidence_refs": []
  }
}""",
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
            logger.warning("business_analysis_llm_failed", ticker=ticker, error=str(exc))
            return None

    def _fallback(
        self,
        company_profile: dict[str, Any],
        segment_evidence: list[dict[str, Any]],
        market_evidence: list[dict[str, Any]],
        competition_evidence: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any]:
        """Deterministic fallback when LLM is unavailable."""

        def _snippets(evidence: list[dict[str, Any]], limit: int = 3) -> list[str]:
            return [str(e.get("text", ""))[:200] for e in evidence[:limit] if e.get("text")]

        def _summary_from_evidence(evidence: list[dict[str, Any]], fallback: str) -> str:
            snippets = _snippets(evidence, limit=2)
            if snippets:
                return " ".join(snippets)
            return fallback or "Insufficient information"

        warnings.append("BusinessAnalyst: LLM unavailable; fell back to rule-based summary.")
        return {
            "business_analysis": {
                "core_judgement": _summary_from_evidence(segment_evidence, company_profile.get("business_model", "")),
                "business_model_detail": _summary_from_evidence(segment_evidence, company_profile.get("business_model", "")),
                "market_space": {
                    "core_judgement": _summary_from_evidence(market_evidence, company_profile.get("market_space", "")),
                    "market_drivers": [],
                    "open_questions": ["Market space analysis requires LLM deep synthesis; currently showing basic profile only."],
                },
                "competition": {
                    "core_judgement": _summary_from_evidence(competition_evidence, company_profile.get("competition_landscape", "")),
                    "key_competitors": [],
                    "competitive_advantages": company_profile.get("competitive_advantages", []),
                    "competitive_weaknesses": company_profile.get("competitive_weaknesses", []),
                    "open_questions": [],
                },
                "supporting_evidence_refs": [
                    {"filing_id": e.get("filing_id"), "section": e.get("section")}
                    for e in (segment_evidence + market_evidence + competition_evidence)[:5]
                ],
                "open_questions": [],
            },
            "segment_analysis": {
                "is_group_company": False,
                "segments": [],
                "segment_level_open_questions": ["Business segment breakdown requires LLM; unable to produce output at this time."],
                "supporting_evidence_refs": [],
            },
            "warnings": warnings,
        }
