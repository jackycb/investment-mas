"""SynthesisReviewer node — Stage 3 quality review and synthesis."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.graph.state import ResearchState
from app.services.aliyun_llm_service import get_llm_service
from app.services.peer_quality_service import PeerSetQualityService
from app.services.rating_guardrail_service import RatingGuardrailService
from app.services.report_coverage_service import ReportCoverageService
from app.services.report_service import ReportService
from app.services.valuation_audit_service import ValuationAuditService

logger = get_logger(__name__)

REVIEW_SYSTEM_PROMPT = """You are a senior investment research reviewer. Review the assembled research output and produce a quality assessment.

Output valid JSON with these exact fields:

{
  "overall_score": 7,
  "overall_assessment": "Brief summary (2-3 sentences) of report quality",
  "section_scores": {
    "business_analysis": {"score": 7, "issues": [], "sufficient": true},
    "culture_governance": {"score": 7, "issues": [], "sufficient": true},
    "peer_analysis": {"score": 7, "issues": [], "sufficient": true},
    "financial_analysis": {"score": 7, "issues": [], "sufficient": true},
    "valuation": {"score": 7, "issues": [], "sufficient": true},
    "risk_analysis": {"score": 7, "issues": [], "sufficient": true},
    "news_analysis": {"score": 7, "issues": [], "sufficient": true}
  },
  "research_questions_answered": ["Question 1"],
  "research_questions_unanswered": ["Question 2"],
  "missing_critical_data": ["filing data for peer HK.00700"],
  "peer_coverage_issues": ["No financial data for peer HK.00700"],
  "should_retry": false,
  "retry_sections": [],
  "retry_reason": "",
  "key_insights": ["Insight 1 from the overall analysis"],
  "actionable_recommendations": ["Recommendation 1"]
}

Scoring rules:
- Score 1-10 for each section (1=missing/empty, 5=basic, 8=good, 10=exceptional)
- "sufficient": true if score >= 5
- "should_retry": true only if critical sections are insufficient AND data exists to improve
- "retry_sections": list of section keys to re-run if should_retry is true
- "missing_critical_data": specific data gaps that prevented thorough analysis

Respond with valid JSON only. No markdown formatting, no code fences."""


class SynthesisReviewerNode:
    """Review assembled research output for quality and completeness.

    This runs after all specialist nodes complete. It evaluates section quality,
    checks whether research questions were answered, and flags missing data.
    """

    def __init__(self, session: Session, model: str | None = None) -> None:
        self.session = session
        self.model = model

    def __call__(self, state: ResearchState) -> dict:
        ticker = state["ticker"]
        company_name = state["company_name"] or ticker
        plan = state.get("research_plan", {})
        warnings = list(state.get("warnings", []))

        # Phase 1: Report coverage check (deterministic quality gate)
        coverage = ReportCoverageService().check(state)

        # Phase 1b: Valuation audit (deterministic)
        valuation_result = state.get("valuation_result", {})
        valuation_assumptions = state.get("valuation_assumptions", {})
        financial_summary = state.get("financial_summary", {})
        valuation_audit = ValuationAuditService().audit(
            valuation_result, valuation_assumptions, financial_summary,
        )

        # Phase 1c: Peer set quality check (deterministic)
        peer_analysis = state.get("peer_analysis", {})
        peer_comparison = state.get("peer_comparison", {})
        peer_quality = PeerSetQualityService().assess(
            peer_analysis, peer_comparison, target_ticker=ticker,
        )

        # Phase 2: Build final report via ReportService
        try:
            markdown, report_json, final_rating = ReportService().build(state, model=self.model)
        except Exception as exc:
            logger.error("synthesis_build_failed", ticker=ticker, error=str(exc))
            markdown = ""
            report_json = {}
            final_rating = "N/A"
            warnings.append(f"Report build failed: {exc}")

        # Phase 3: Rating guardrails (deterministic post-LLM check)
        business_analysis = state.get("business_analysis", {})
        segment_analysis = state.get("segment_analysis", {})
        culture_analysis = state.get("culture_analysis", {})
        risk_assessment = state.get("risk_assessment", {})

        llm_confidence = None
        if isinstance(report_json, dict):
            rec = report_json.get("sections", {}).get("Final Recommendation", "")
            if isinstance(rec, str) and "Confidence:" in rec:
                try:
                    conf_str = rec.split("Confidence:")[-1].strip().split()[0]
                    llm_confidence = float(conf_str)
                except (ValueError, IndexError):
                    pass

        guardrail_result = RatingGuardrailService().apply(
            proposed_rating=final_rating,
            valuation_audit=valuation_audit,
            peer_quality=peer_quality,
            financial_summary=financial_summary,
            risk_assessment=risk_assessment,
            business_analysis=business_analysis,
            segment_analysis=segment_analysis,
            culture_analysis=culture_analysis,
            report_confidence=llm_confidence,
        )

        if guardrail_result["rating_changed"]:
            final_rating = guardrail_result["final_rating"]
            if isinstance(report_json, dict):
                report_json["final_rating"] = final_rating
                report_json["final_rating_label"] = self._rating_label(final_rating)
            warnings.append(
                f"Rating guardrail: downgraded from '{guardrail_result['proposed_rating']}' to "
                f"'{final_rating}' due to: "
                + "; ".join(c["rule"] for c in guardrail_result["constraints_triggered"])
            )

        # Inject audit/quality data into report_json
        if isinstance(report_json, dict):
            report_json["valuation_audit"] = valuation_audit
            report_json["data_quality_check"] = {
                "peer_set_quality": peer_quality.get("peer_set_quality", {}),
                "valuation_audit_passed": valuation_audit.get("passed", False),
                "valuation_issues": valuation_audit.get("issues", []),
            }
            report_json["rating_decision"] = {
                **guardrail_result.get("decision_scores", {}),
                "rating_constraints_triggered": [
                    c["rule"] for c in guardrail_result.get("constraints_triggered", [])
                ],
            }

        # Inject research_questions from plan into report_json
        questions = plan.get("research_questions", [])
        if questions and isinstance(report_json, dict):
            report_json["research_questions"] = questions

        # Compute heuristic scores for synthesis_review
        events = state.get("event_list", [])
        news = state.get("company_news", [])
        errors = state.get("errors", [])
        data_availability = state.get("data_availability", {})

        heuristic_scores = self._heuristic_scores(
            plan, business_analysis, segment_analysis, culture_analysis,
            peer_analysis, financial_summary, peer_comparison,
            valuation_result, risk_assessment, events, news,
            data_availability,
        )

        llm_review = self._llm_review(
            ticker, company_name, plan,
            business_analysis, segment_analysis, culture_analysis,
            peer_analysis, financial_summary, peer_comparison,
            valuation_result, risk_assessment, events, news,
            data_availability, errors,
        )

        if llm_review:
            review = llm_review
        else:
            review = heuristic_scores

        peer_coverage_issues = self._detect_peer_issues(data_availability, peer_analysis, peer_comparison)
        review["peer_coverage_issues"] = peer_coverage_issues
        review["valuation_audit"] = valuation_audit
        review["peer_set_quality"] = peer_quality.get("peer_set_quality", {})
        review["rating_guardrail"] = guardrail_result

        # Collect coverage warnings
        coverage_warnings = coverage.get("warnings", [])
        all_warnings = warnings + review.get("missing_critical_data", []) + coverage_warnings
        for issue in valuation_audit.get("issues", []):
            all_warnings.append(f"Valuation audit: {issue}")

        logger.info(
            "synthesis_review_complete",
            ticker=ticker,
            overall_score=review.get("overall_score", 0),
            should_retry=review.get("should_retry", False),
            valuation_audit_passed=valuation_audit.get("passed"),
            rating_guardrail_changed=guardrail_result.get("rating_changed"),
        )

        return {
            "synthesis_review": review,
            "final_report": markdown,
            "final_rating": final_rating,
            "report_json": report_json,
            "report_coverage": coverage,
            "warnings": all_warnings,
        }

    @staticmethod
    def _rating_label(rating: str) -> str:
        return {
            "buy": "Buy",
            "watch": "Watch",
            "avoid": "Avoid",
            "research_more": "Research More",
        }.get(rating, "Research More")

    def _heuristic_scores(
        self,
        plan: dict,
        business_analysis: dict,
        segment_analysis: dict,
        culture_analysis: dict,
        peer_analysis: dict,
        financial_summary: dict,
        peer_comparison: dict,
        valuation_result: dict,
        risk_assessment: dict,
        events: list,
        news: list,
        data_availability: dict,
    ) -> dict:
        """Compute heuristic scores based on section content presence."""
        sections_plan = plan.get("sections", {})

        def score_section(data: dict | list | None, section_key: str) -> dict:
            """Score a section based on whether it has content."""
            plan_cfg = sections_plan.get(section_key, {})
            skipped = plan_cfg.get("skip", False)

            if skipped:
                return {"score": 10, "issues": [], "sufficient": True}  # Intentionally skipped = OK

            if data is None or (isinstance(data, dict) and not data) or (isinstance(data, list) and not data):
                return {"score": 1, "issues": ["No data produced"], "sufficient": False}

            if isinstance(data, dict):
                key_count = len(data)
                if key_count >= 3:
                    score = min(10, 5 + key_count)
                elif key_count >= 1:
                    score = 4
                else:
                    score = 2
            elif isinstance(data, list):
                if len(data) >= 3:
                    score = min(10, 5 + len(data))
                elif len(data) >= 1:
                    score = 4
                else:
                    score = 2
            else:
                score = 5

            return {"score": score, "issues": [], "sufficient": score >= 5}

        section_scores = {
            "business_analysis": score_section(business_analysis, "business_analysis"),
            "culture_governance": score_section(culture_analysis, "culture_governance"),
            "peer_analysis": score_section(peer_analysis, "peer_analysis"),
            "financial_analysis": score_section(financial_summary, "financial_analysis"),
            "valuation": score_section(valuation_result, "valuation"),
            "risk_analysis": score_section(risk_assessment, "risk_analysis"),
            "news_analysis": score_section(events or news, "news_analysis"),
        }

        scores = [s["score"] for s in section_scores.values()]
        overall_score = round(sum(scores) / len(scores)) if scores else 5

        return {
            "overall_score": overall_score,
            "overall_assessment": f"Heuristic review: {len([s for s in scores if s >= 5])}/{len(scores)} sections sufficient.",
            "section_scores": section_scores,
            "research_questions_answered": [],
            "research_questions_unanswered": plan.get("research_questions", []),
            "missing_critical_data": [],
            "peer_coverage_issues": [],
            "should_retry": False,
            "retry_sections": [],
            "retry_reason": "",
            "key_insights": [],
            "actionable_recommendations": [],
        }

    def _llm_review(
        self,
        ticker: str,
        company_name: str,
        plan: dict,
        business_analysis: dict,
        segment_analysis: dict,
        culture_analysis: dict,
        peer_analysis: dict,
        financial_summary: dict,
        peer_comparison: dict,
        valuation_result: dict,
        risk_assessment: dict,
        events: list,
        news: list,
        data_availability: dict,
        errors: list,
    ) -> dict | None:
        """Use LLM for deeper quality review."""
        llm = get_llm_service()
        if not llm.available:
            logger.info("synthesis_reviewer_llm_unavailable", ticker=ticker)
            return None

        # Build a compact summary for the LLM
        summary = {
            "company": f"{company_name} ({ticker})",
            "plan": {
                "sections": {
                    k: {"priority": v.get("priority", "high"), "depth": v.get("depth", "standard")}
                    for k, v in plan.get("sections", {}).items()
                },
                "research_questions": plan.get("research_questions", []),
            },
            "data_available": {
                "target_filings": data_availability.get("target", {}).get("filings", {}).get("status", "unknown"),
                "peer_filings_available": [
                    k for k, v in data_availability.get("peers", {}).items()
                    if v.get("filings", {}).get("status") == "available"
                ],
                "news": data_availability.get("news", {}).get("status", "unknown"),
            },
            "output_summary": {
                "business_analysis_keys": list(business_analysis.keys()) if business_analysis else [],
                "segment_analysis_keys": list(segment_analysis.keys()) if segment_analysis else [],
                "culture_analysis_keys": list(culture_analysis.keys()) if culture_analysis else [],
                "peer_analysis_keys": list(peer_analysis.keys()) if peer_analysis else [],
                "financial_summary_keys": list(financial_summary.keys()) if financial_summary else [],
                "peer_comparison_keys": list(peer_comparison.keys()) if peer_comparison else [],
                "valuation_result_keys": list(valuation_result.keys()) if valuation_result else [],
                "risk_assessment_keys": list(risk_assessment.keys()) if risk_assessment else [],
                "event_count": len(events) if events else 0,
                "news_count": len(news) if news else 0,
            },
            "errors": errors[:5],
        }

        user_message = f"Review this research output:\n{__import__('json').dumps(summary, indent=2, ensure_ascii=False)}"
        messages = [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        try:
            response = llm.chat(messages=messages, temperature=0.3, model=self.model)
            content = response.content if hasattr(response, "content") else str(response)
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:]) if len(lines) > 1 else content
            if content.endswith("```"):
                content = content.rsplit("```", 1)[0].strip()

            import json
            review = json.loads(content)

            # Validate required keys
            required_keys = ["overall_score", "section_scores", "should_retry"]
            if not all(k in review for k in required_keys):
                logger.warning("synthesis_reviewer_missing_keys", ticker=ticker)
                return None

            logger.info("synthesis_reviewer_llm_success", ticker=ticker, score=review.get("overall_score"))
            return review

        except (__import__('json').JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("synthesis_reviewer_llm_parse_failed", ticker=ticker, error=str(exc))
            return None
        except Exception as exc:
            logger.error("synthesis_reviewer_llm_error", ticker=ticker, error=str(exc))
            return None

    def _detect_peer_issues(
        self,
        data_availability: dict,
        peer_analysis: dict,
        peer_comparison: dict,
    ) -> list[str]:
        """Detect peer coverage issues from data availability."""
        issues: list[str] = []
        peers_data = data_availability.get("peers", {})
        for ticker, data in peers_data.items():
            if data.get("filings", {}).get("status") != "available":
                issues.append(f"No filing data for peer {ticker}")
        if not peer_analysis and not peer_comparison:
            issues.append("No peer analysis or comparison data produced")
        return issues