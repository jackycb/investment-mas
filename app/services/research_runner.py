"""Research workflow execution and persistence helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Company, NewsArticle, NewsEvent, ResearchReport, RiskAssessment, ValuationResult

logger = get_logger(__name__)
from app.db.repositories.company_repository import get_company_by_ticker, get_or_create_company
from app.db.repositories.document_repository import get_company_documents
from app.db.repositories.filing_repository import get_effective_filings, get_filings
from app.providers.company_info_provider import get_canonical_company_name
from app.utils.metadata_utils import normalize_ticker
from app.graph.nodes.business_analyst import BusinessAnalystNode
from app.graph.nodes.company_profiler import CompanyProfilerNode
from app.graph.nodes.culture_analyst import CultureAnalystNode
from app.graph.nodes.filing_coverage_checker import FilingCoverageCheckerNode
from app.graph.nodes.financial_analyst import FinancialAnalystNode
from app.graph.nodes.investment_expert import InvestmentExpertNode
from app.graph.nodes.news_collector import NewsCollectorNode
from app.graph.nodes.peer_analyzer import PeerAnalyzerNode
from app.graph.nodes.report_coverage_checker import ReportCoverageCheckerNode
from app.graph.nodes.risk_analyst import RiskAnalystNode
from app.graph.nodes.valuation_agent import ValuationAgentNode
from app.graph.state import ResearchState, create_initial_state
from app.schemas.research import ResearchRunResponse
from app.services.aliyun_llm_service import get_llm_service
from app.services.report_service import ReportService

ProgressCallback = Callable[[str, str, dict[str, Any] | None], None]

WORKFLOW_STEPS: list[tuple[str, str, type]] = [
    ("company_profiler", "CompanyProfiler", CompanyProfilerNode),
    ("filing_coverage_checker", "FilingCoverageChecker", FilingCoverageCheckerNode),
    ("business_analyst", "BusinessAnalyst", BusinessAnalystNode),
    ("culture_analyst", "CultureAnalyst", CultureAnalystNode),
    ("peer_analyzer", "PeerAnalyzer", PeerAnalyzerNode),
    ("news_collector", "NewsCollector", NewsCollectorNode),
    ("financial_analyst", "FinancialAnalyst", FinancialAnalystNode),
    ("valuation_agent", "ValuationAgent", ValuationAgentNode),
    ("risk_analyst", "RiskAnalyst", RiskAnalystNode),
    ("investment_expert", "InvestmentExpert", InvestmentExpertNode),
    ("report_coverage_checker", "ReportCoverageChecker", ReportCoverageCheckerNode),
]


@dataclass
class ResearchExecution:
    """Result of a workflow execution."""

    state: ResearchState
    response: ResearchRunResponse


class ResearchRunner:
    """Run the research workflow sequentially with persistence hooks."""

    def __init__(self, session: Session, model: str | None = None) -> None:
        self.session = session
        self.model = model

    def run(
        self,
        ticker: str,
        company_name: str,
        progress_callback: ProgressCallback | None = None,
    ) -> ResearchExecution:
        """Execute all workflow steps and persist outputs."""

        ticker = normalize_ticker(ticker)
        company_name = get_canonical_company_name(ticker, company_name)
        state = create_initial_state(ticker, company_name)
        for step_id, step_name, node_cls in WORKFLOW_STEPS:
            if progress_callback:
                progress_callback(step_id, "started", {"label": step_name})
            node = self._build_node(step_id, node_cls)
            result = node(state)
            state.update(result)
            if step_id == "news_collector":
                self._persist_news_and_events(state)
            self._commit_checkpoint(step_id)
            if progress_callback:
                progress_callback(
                    step_id,
                    "completed",
                    {
                        "label": step_name,
                        "errors": state.get("errors", [])[-3:],
                        "details": self._build_step_details(step_id, node, state),
                    },
                )

        company = self._get_or_create_company(ticker, company_name)
        response = self._persist_outputs(company, state)
        if progress_callback:
            progress_callback("workflow", "completed", {"final_rating": response.final_rating})
        return ResearchExecution(state=state, response=response)

    def run_stream(self, ticker: str, company_name: str) -> Iterator[dict[str, Any]]:
        """Execute the workflow and yield progress events in order."""

        ticker = normalize_ticker(ticker)
        company_name = get_canonical_company_name(ticker, company_name)
        state = create_initial_state(ticker, company_name)
        yield {"event": "started", "data": {"ticker": ticker, "company_name": company_name}}
        for step_id, step_name, node_cls in WORKFLOW_STEPS:
            yield {"event": "progress", "data": {"step": step_id, "status": "started", "payload": {"label": step_name}}}
            node = self._build_node(step_id, node_cls)
            result = node(state)
            state.update(result)
            if step_id == "news_collector":
                self._persist_news_and_events(state)
            self._commit_checkpoint(step_id)
            yield {
                "event": "progress",
                "data": {
                    "step": step_id,
                    "status": "completed",
                    "payload": {
                        "label": step_name,
                        "errors": state.get("errors", [])[-3:],
                        "details": self._build_step_details(step_id, node, state),
                    },
                },
            }
        company = self._get_or_create_company(ticker, company_name)
        response = self._persist_outputs(company, state)
        yield {"event": "result", "data": response.model_dump()}
        yield {"event": "done", "data": {"ticker": response.ticker}}

    def _build_node(self, step_id: str, node_cls: type) -> Any:
        """Build a workflow step node with the configured model."""
        if step_id == "company_profiler":
            return node_cls(self.session, model=self.model)
        if step_id in {"filing_coverage_checker", "financial_analyst", "risk_analyst"}:
            return node_cls(self.session)
        if step_id in {"business_analyst", "culture_analyst"}:
            return node_cls(self.session, model=self.model)
        if step_id == "peer_analyzer":
            return node_cls(model=self.model)
        if step_id == "investment_expert":
            return node_cls(model=self.model)
        if step_id == "news_collector":
            return node_cls(self.session, model=self.model)
        return node_cls()

    def _get_or_create_company(self, ticker: str, company_name: str) -> Company:
        return get_or_create_company(self.session, ticker, company_name)

    def _persist_news_and_events(self, state: ResearchState) -> None:
        """Store fetched news and extracted events for later reuse."""

        company = self._get_or_create_company(state["ticker"], state["company_name"])
        article_lookup: dict[tuple[str, str], NewsArticle] = {}
        for article in state.get("company_news", []):
            stored = self._upsert_article(company, article)
            for key in self._article_lookup_keys(article):
                article_lookup[(article.get("ticker", "").upper(), key)] = stored

        for peer in state.get("peer_list", []):
            peer_ticker = str(peer.get("ticker", "")).upper()
            if not peer_ticker:
                continue
            peer_company = self._get_or_create_company(peer_ticker, peer.get("company_name") or peer_ticker)
            for article in [item for item in state.get("peer_news", []) if str(item.get("ticker", "")).upper() == peer_ticker]:
                stored = self._upsert_article(peer_company, article)
                for key in self._article_lookup_keys(article):
                    article_lookup[(peer_ticker, key)] = stored

        for event in state.get("event_list", []):
            event_ticker = str(event.get("ticker") or state["ticker"]).upper()
            event_company = self._get_or_create_company(
                event_ticker,
                state["company_name"] if event_ticker == state["ticker"] else event_ticker,
            )
            event_key = (
                event_company.id,
                str(event.get("event_type", "")),
                str(event.get("event_summary", "")),
                self._coerce_date(event.get("event_date")),
            )
            existing = self.session.scalar(
                select(NewsEvent).where(
                    NewsEvent.company_id == event_key[0],
                    NewsEvent.event_type == event_key[1],
                    NewsEvent.event_summary == event_key[2],
                    NewsEvent.event_date == event_key[3],
                )
            )
            if existing:
                continue
            article_ref = article_lookup.get((event_ticker, str(event.get("event_summary", "")))) or article_lookup.get(
                (event_ticker, str(event.get("title", "")))
            )
            self.session.add(
                NewsEvent(
                    article_id=article_ref.id if article_ref else None,
                    company_id=event_company.id,
                    event_date=event_key[3],
                    event_type=event_key[1] or "other",
                    sentiment=event.get("sentiment"),
                    importance_score=event.get("importance_score"),
                    confidence_score=event.get("confidence_score"),
                    risk_tags=event.get("risk_tags", []),
                    entities=event.get("entities", []),
                    event_summary=event_key[2],
                    impact_dimensions=event.get("impact_dimensions", {}),
                    created_at=datetime.now(timezone.utc),
                )
            )
        self.session.flush()

    def _upsert_article(self, company: Company, article: dict[str, Any]) -> NewsArticle:
        key_url = self._normalize_article_url(article.get("url"))
        key_title = self._safe_varchar(article.get("title"), 500)
        existing = None
        if key_url:
            existing = self.session.scalar(
                select(NewsArticle).where(NewsArticle.company_id == company.id, NewsArticle.url == key_url)
            )
        if existing is None and key_title:
            existing = self.session.scalar(
                select(NewsArticle).where(NewsArticle.company_id == company.id, NewsArticle.title == key_title)
            )
        published_at = self._coerce_datetime(article.get("published_at"))
        if existing:
            existing.summary = self._safe_text(article.get("summary"))
            existing.content = self._safe_text(article.get("content"))
            existing.source_name = self._safe_varchar(article.get("source_name"), 128)
            existing.source_type = self._safe_varchar(article.get("source_type"), 32) or existing.source_type
            existing.author = self._safe_varchar(article.get("author"), 128)
            existing.language = self._safe_varchar(article.get("language", "en"), 32)
            existing.published_at = published_at or existing.published_at
            existing.fetched_at = datetime.now(timezone.utc)
            existing.url = key_url or existing.url
            return existing

        stored = NewsArticle(
            company_id=company.id,
            title=key_title or "Untitled Article",
            source_name=self._safe_varchar(article.get("source_name"), 128),
            source_type=self._safe_varchar(article.get("source_type"), 32) or "finance_news",
            url=key_url,
            published_at=published_at,
            author=self._safe_varchar(article.get("author"), 128),
            content=self._safe_text(article.get("content")),
            summary=self._safe_text(article.get("summary")),
            language=self._safe_varchar(article.get("language", "en"), 32),
            raw_html=self._safe_text(article.get("raw_html")),
            fetched_at=datetime.now(timezone.utc),
        )
        self.session.add(stored)
        self.session.flush()
        return stored

    def _persist_outputs(self, company: Company, state: ResearchState) -> ResearchRunResponse:
        report_markdown = state.get("final_report", "")
        report_json = state.get("report_json", {})
        final_rating = state.get("final_rating", "research_more")
        if not report_markdown or not report_json:
            report_markdown, report_json, final_rating = ReportService().build(state, model=self.model)

        valuation = state.get("valuation_result", {})
        blended = valuation.get("blended", {})
        self.session.add(
            ValuationResult(
                company_id=company.id,
                valuation_date=date.today(),
                valuation_method="blended_per_share_v2",
                bull_value=blended.get("bull"),
                base_value=blended.get("base"),
                bear_value=blended.get("bear"),
                assumptions=state.get("valuation_assumptions", {}),
                sensitivity={
                    "methods": list(valuation.keys()),
                    "per_share_currency": valuation.get("per_share_currency"),
                    "shares_outstanding": valuation.get("shares_outstanding"),
                },
                conclusion=f"Per-share valuation in {valuation.get('per_share_currency', 'N/A')}.",
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.add(
            RiskAssessment(
                company_id=company.id,
                assessment_date=date.today(),
                fundamentals_risk_score=state.get("risk_assessment", {}).get("fundamentals_risk_score"),
                competition_risk_score=state.get("risk_assessment", {}).get("competition_risk_score"),
                governance_risk_score=state.get("risk_assessment", {}).get("governance_risk_score"),
                event_risk_score=state.get("risk_assessment", {}).get("event_risk_score"),
                valuation_risk_score=state.get("risk_assessment", {}).get("valuation_risk_score"),
                overall_risk_score=state.get("risk_assessment", {}).get("overall_risk_score"),
                red_flags=state.get("risk_assessment", {}).get("red_flags", []),
                key_risks=state.get("risk_assessment", {}).get("key_risks", []),
                monitoring_signals=state.get("risk_assessment", {}).get("monitoring_signals", []),
                recommendation=state.get("risk_assessment", {}).get("recommendation", ""),
                supporting_evidence=state.get("risk_assessment", {}).get("supporting_evidence", []),
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.add(
            ResearchReport(
                company_id=company.id,
                report_date=date.today(),
                title=f"Investment Research Report - {company.company_name} ({company.ticker})",
                executive_summary=str(report_json.get("executive_summary", "")),
                report_markdown=report_markdown,
                report_json=report_json,
                final_rating=final_rating,
                created_at=datetime.now(timezone.utc),
            )
        )
        self.session.commit()
        self._save_report_files(company.ticker, report_markdown, report_json)
        return ResearchRunResponse(
            ticker=company.ticker,
            company_name=company.company_name,
            final_rating=final_rating,
            executive_summary=str(report_json.get("executive_summary", "")),
            report_markdown=report_markdown,
            report_json=report_json,
        )

    def _save_report_files(self, ticker: str, report_markdown: str, report_json: dict) -> None:
        """Save report as .docx and .md to data/results/{market}/{ticker}/."""

        from app.core.config import DATA_ROOT
        from app.services.docx_export_service import markdown_report_to_docx
        from app.utils.metadata_utils import infer_market_from_ticker

        market = infer_market_from_ticker(ticker)
        results_dir = DATA_ROOT / "results" / market / ticker
        results_dir.mkdir(parents=True, exist_ok=True)
        date_str = date.today().isoformat()
        stem = f"{ticker.replace('.', '_')}_{date_str}"

        md_path = results_dir / f"{stem}.md"
        md_path.write_text(report_markdown, encoding="utf-8")

        try:
            buf = markdown_report_to_docx(report_markdown, report_json)
            docx_path = results_dir / f"{stem}.docx"
            docx_path.write_bytes(buf.getvalue())
            logger.info("report_saved", ticker=ticker, md=str(md_path), docx=str(docx_path))
        except Exception as exc:
            logger.warning("docx_save_failed", ticker=ticker, error=str(exc))

    def _commit_checkpoint(self, step_id: str) -> None:
        """Persist step-level side effects early so streaming runs don't lose filings/news on later interruption."""

        if step_id not in {"company_profiler", "news_collector", "filing_coverage_checker"}:
            return
        if os.getenv("PYTEST_CURRENT_TEST"):
            return
        self.session.commit()
        self.session.expire_all()

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and value:
            normalized = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def _article_lookup_keys(self, article: dict[str, Any]) -> list[str]:
        """Generate candidate lookup keys for article-to-event linking."""

        keys = [article.get("summary"), article.get("title"), article.get("url")]
        return [str(item) for item in keys if item]

    def _safe_varchar(self, value: Any, limit: int) -> str | None:
        """Trim string values to fit VARCHAR columns."""

        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:limit]

    def _safe_text(self, value: Any) -> str | None:
        """Normalize optional text payloads."""

        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_article_url(self, value: Any) -> str | None:
        """Shorten noisy aggregator URLs so they fit storage and dedupe better."""

        raw = self._safe_text(value)
        if raw is None:
            return None

        parsed = urlparse(raw)
        if "news.google.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            for key in ("url", "u"):
                candidate = qs.get(key, [None])[0]
                if candidate:
                    normalized = self._safe_text(unquote(candidate))
                    if normalized:
                        return normalized[:500]
        if len(raw) <= 500:
            return raw

        shortened = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else raw
        if len(shortened) <= 500:
            return shortened
        return shortened[:500]

    def _build_step_details(self, step_id: str, node: Any, state: ResearchState) -> dict[str, Any]:
        """Build human-friendly execution details for the frontend run log."""

        if step_id == "company_profiler":
            provider = getattr(getattr(node, "service", None), "provider", None)
            raw_sources = state.get("company_profile", {}).get("raw_sources", [])
            llm = getattr(provider, "llm", None)
            company = get_company_by_ticker(self.session, state.get("ticker", ""))
            documents = get_company_documents(self.session, company.id) if company else []
            filings = get_filings(self.session, company.id) if company else []
            effective_filings = get_effective_filings(self.session, company.id) if company else []
            document_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "company_document"]
            evidence_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "document_rag"]
            filing_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "filing"]
            filing_rag_sources = [item for item in raw_sources if isinstance(item, dict) and item.get("source") == "filing_rag"]
            document_topics = sorted(
                {
                    topic
                    for item in [*evidence_sources, *filing_rag_sources]
                    for topic in (item.get("topics") or [])
                    if isinstance(topic, str) and topic.strip()
                }
            )
            peer_samples = [
                {
                    "ticker": peer.get("ticker"),
                    "peer_type": peer.get("peer_type"),
                    "rationale": peer.get("rationale"),
                }
                for peer in state.get("peer_list", [])[:4]
                if isinstance(peer, dict)
            ]
            warnings = state.get("company_profile", {}).get("warnings", [])
            filing_status = "enabled"
            if any("skipped filing ingestion" in str(item).lower() or "tables not created" in str(item).lower() for item in warnings):
                filing_status = "degraded"
            elif len(effective_filings) == 0:
                filing_status = "no_results"
            filing_status_label = {
                "enabled": "Enabled",
                "degraded": "Degraded",
                "no_results": "Enabled, but no usable disclosure files found yet",
            }.get(filing_status, filing_status)
            return {
                "provider": provider.__class__.__name__ if provider else None,
                "sources": raw_sources,
                "peer_count": len(state.get("peer_list", [])),
                "is_mock": any("mock" in str(item.get("source", "")).lower() for item in raw_sources if isinstance(item, dict)),
                "model": self._model_info(llm),
                "warnings": warnings,
                "filing_status": filing_status,
                "filing_status_label": filing_status_label,
                "document_count": len(documents),
                "filing_count": len(effective_filings),
                "filing_total_count": len(filings),
                "document_types": sorted({str(item.doc_type) for item in documents if item.doc_type}),
                "filing_types": sorted({str(item.filing_type) for item in effective_filings if item.filing_type}),
                "document_years": [item.fiscal_year for item in documents if item.fiscal_year is not None][:8],
                "filing_years": [item.fiscal_year for item in effective_filings if item.fiscal_year is not None][:8],
                "document_sources": [
                    {
                        "doc_type": item.get("doc_type"),
                        "fiscal_year": item.get("fiscal_year"),
                        "source_name": item.get("source_name"),
                    }
                    for item in document_sources[:8]
                ],
                "filing_sources": [
                    {
                        "filing_type": item.get("filing_type"),
                        "fiscal_year": item.get("fiscal_year"),
                        "source_name": item.get("source_name"),
                    }
                    for item in filing_sources[:8]
                ],
                "effective_filing_sources": [
                    {
                        "filing_type": item.filing_type,
                        "fiscal_year": item.fiscal_year,
                        "source_name": item.source_name,
                        "file_type": item.file_type,
                    }
                    for item in effective_filings[:8]
                ],
                "evidence_topics": document_topics,
                "evidence_bundle_count": len(evidence_sources) + len(filing_rag_sources),
                "peer_samples": peer_samples,
                "profile_source": "annual_report_only" if effective_filings else "unavailable",
            }
        if step_id == "news_collector":
            articles = state.get("company_news", []) + state.get("peer_news", [])
            source_names = sorted({str(item.get("source_name")) for item in articles if item.get("source_name")})
            source_types = sorted({str(item.get("source_type")) for item in articles if item.get("source_type")})
            return {
                "company_news_count": len(state.get("company_news", [])),
                "peer_news_count": len(state.get("peer_news", [])),
                "event_count": len(state.get("event_list", [])),
                "sources": source_names,
                "source_names": source_names,
                "source_types": source_types,
                "is_mock": any(self._article_is_mock(item) for item in articles),
            }
        if step_id == "financial_analyst":
            summary = state.get("financial_summary", {})
            return {
                "provider": summary.get("provider_name"),
                "provider_name": summary.get("provider_name"),
                "data_source": summary.get("data_source_label"),
                "source_detail": summary.get("source_detail", {}),
                "years": summary.get("years", []),
                "years_count": len(summary.get("years", [])),
                "red_flags": state.get("financial_red_flags", []),
                "data_status": "unavailable" if summary.get("data_source") == "unavailable" else "available",
                "is_mock": False,
            }
        if step_id == "valuation_agent":
            valuation = state.get("valuation_result", {})
            supplemental = valuation.get("legacy_value_analysis", {})
            blended = valuation.get("blended", {})
            return {
                "methods": list(valuation.keys()),
                "per_share_currency": valuation.get("per_share_currency"),
                "blended_bear": blended.get("bear"),
                "blended_base": blended.get("base"),
                "blended_bull": blended.get("bull"),
                "primary_base": valuation.get("primary_value", {}).get("base"),
                "growth_base": valuation.get("growth", {}).get("base"),
                "shares_outstanding": valuation.get("shares_outstanding"),
                "supplemental_method": supplemental.get("method"),
                "supplemental_status": supplemental.get("status"),
                "supplemental_lower": supplemental.get("lower_price_limit"),
                "supplemental_upper": supplemental.get("upper_price_limit"),
                "is_mock": False,
            }
        if step_id == "risk_analyst":
            risk = state.get("risk_assessment", {})
            return {
                "overall_risk_score": risk.get("overall_risk_score"),
                "recommendation": risk.get("recommendation"),
                "red_flag_count": len(risk.get("red_flags", [])),
                "is_mock": False,
            }
        if step_id == "investment_expert":
            llm = getattr(getattr(node, "service", None), "llm_service", None) or get_llm_service()
            return {
                "final_rating": state.get("final_rating"),
                "final_rating_label": state.get("report_json", {}).get("final_rating_label"),
                "report_language": "en",
                "model": self._model_info(llm),
                "is_mock": not bool(getattr(llm, "available", False)),
            }
        if step_id == "filing_coverage_checker":
            coverage = state.get("filing_coverage", {})
            return {
                "overall_coverage": coverage.get("overall_coverage"),
                "covered_topics": list(coverage.get("covered", {}).keys()),
                "missing_topics": coverage.get("missing", []),
            }
        if step_id == "business_analyst":
            ba = state.get("business_analysis", {})
            sa = state.get("segment_analysis", {})
            return {
                "has_business_analysis": bool(ba.get("core_judgement")),
                "is_group_company": sa.get("is_group_company", False),
                "segment_count": len(sa.get("segments", [])),
            }
        if step_id == "culture_analyst":
            ca = state.get("culture_analysis", {})
            return {
                "has_culture_analysis": bool(ca.get("core_judgement")),
                "management_member_count": len(ca.get("management_members", [])),
            }
        if step_id == "peer_analyzer":
            pa = state.get("peer_analysis", {})
            return {
                "core_peer_count": len(pa.get("core_peers", [])),
                "secondary_peer_count": len(pa.get("secondary_peers", [])),
                "valuation_peer_count": len(pa.get("valuation_peers", [])),
            }
        if step_id == "report_coverage_checker":
            rc = state.get("report_coverage", {})
            return {
                "passed": rc.get("passed", False),
                "warning_count": len(rc.get("warnings", [])),
            }
        return {}

    def _model_info(self, llm: Any) -> dict[str, Any] | None:
        """Return logging-safe LLM info."""

        if llm is None:
            return None
        config = getattr(llm, "config", None)
        if config is None:
            return {"enabled": bool(getattr(llm, "available", False))}
        return {
            "enabled": bool(getattr(llm, "available", False)),
            "provider": getattr(config, "provider", None),
            "model": getattr(config, "model", None),
            "base_url": getattr(config, "base_url", None),
        }

    def _article_is_mock(self, article: dict[str, Any]) -> bool:
        """Detect whether an article likely comes from a mock source."""

        source_name = str(article.get("source_name", "")).lower()
        url = str(article.get("url", "")).lower()
        return source_name.startswith("mock") or "example.com" in url

    def _coerce_date(self, value: Any) -> date | None:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        parsed = self._coerce_datetime(value)
        return parsed.date() if parsed else None
