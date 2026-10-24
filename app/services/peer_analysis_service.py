"""Peer classification and explanation service."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service

logger = get_logger(__name__)


class PeerAnalysisService:
    """Classify and explain peer relationships using evidence and LLM."""

    def __init__(self, llm_service: AliyunLLMService | None = None) -> None:
        self.llm_service = llm_service or get_llm_service()

    def analyze(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        peer_list: list[dict[str, Any]],
        business_analysis: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        if not peer_list:
            return {
                "core_peers": [],
                "secondary_peers": [],
                "valuation_peers": [],
                "peer_details": [],
                "open_questions": ["No peer candidates found."],
            }

        llm_result = self._synthesize(ticker, company_name, company_profile, peer_list, business_analysis, model=model)
        if llm_result is not None:
            return llm_result

        return self._fallback(peer_list)

    def _synthesize(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        peer_list: list[dict[str, Any]],
        business_analysis: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.llm_service.available:
            return None
        try:
            from app.services.llm_prompt_templates import PEER_EXPLANATION_SYSTEM_PROMPT
            import json

            candidates = [
                {
                    "ticker": p.get("ticker"),
                    "company_name": p.get("company_name"),
                    "peer_type": p.get("peer_type", "core"),
                    "source": p.get("source", ""),
                    "similarity_score": p.get("similarity_score"),
                }
                for p in peer_list[:15]
            ]

            competition_info = ""
            if isinstance(business_analysis, dict):
                comp = business_analysis.get("competition", {})
                if isinstance(comp, dict):
                    competition_info = comp.get("core_judgement", "")

            user_payload = {
                "company_name": company_name,
                "ticker": ticker,
                "company_profile": {
                    k: company_profile.get(k)
                    for k in ("business_model", "revenue_model", "market_space", "competition_landscape", "key_products")
                },
                "competition_context": competition_info,
                "peer_candidates": candidates,
                "task": """Classify and explain the given peer candidates. Output JSON:
{
  "core_peers": [{"ticker": "", "company_name": "", "rationale": "", "similarity_dimensions": []}],
  "secondary_peers": [{"ticker": "", "company_name": "", "rationale": "", "similarity_dimensions": []}],
  "valuation_peers": [{"ticker": "", "company_name": "", "rationale": "", "similarity_dimensions": []}],
  "peer_details": [{"ticker": "", "company_name": "", "peer_type": "", "rationale": "", "confidence": 0.0}],
  "open_questions": []
}

Requirements: Keep all candidates; do not add or remove any. Choose peer_type from core/secondary/valuation/weakly_related.""",
            }

            messages = [
                {"role": "system", "content": PEER_EXPLANATION_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
            result = self.llm_service.chat_json(messages, model=model)
            if isinstance(result, dict):
                return result
            return None
        except Exception as exc:
            logger.warning("peer_analysis_llm_failed", ticker=ticker, error=str(exc))
            return None

    def _fallback(self, peer_list: list[dict[str, Any]]) -> dict[str, Any]:
        core = [p for p in peer_list if p.get("peer_type") == "core"]
        secondary = [p for p in peer_list if p.get("peer_type") == "secondary"]
        valuation = [p for p in peer_list if p.get("peer_type") == "valuation"]
        other = [p for p in peer_list if p.get("peer_type") not in ("core", "secondary", "valuation")]

        def _to_detail(peers: list[dict[str, Any]], ptype: str) -> list[dict[str, Any]]:
            return [
                {"ticker": p.get("ticker"), "company_name": p.get("company_name"), "peer_type": ptype, "rationale": p.get("rationale", ""), "confidence": p.get("similarity_score", 0.5)}
                for p in peers
            ]

        return {
            "core_peers": [{"ticker": p.get("ticker"), "company_name": p.get("company_name"), "rationale": p.get("rationale", "")} for p in core],
            "secondary_peers": [{"ticker": p.get("ticker"), "company_name": p.get("company_name"), "rationale": p.get("rationale", "")} for p in secondary],
            "valuation_peers": [{"ticker": p.get("ticker"), "company_name": p.get("company_name"), "rationale": p.get("rationale", "")} for p in valuation],
            "peer_details": _to_detail(core, "core") + _to_detail(secondary, "secondary") + _to_detail(valuation, "valuation") + _to_detail(other, "weakly_related"),
            "open_questions": ["Peer classification relies on LLM; currently grouped directly by original peer_type."],
        }
