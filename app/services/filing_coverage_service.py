"""Filing evidence coverage checking service."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, not_, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Filing, FilingChunk
from app.db.repositories.company_repository import get_company_by_ticker
from app.db.repositories.filing_repository import NON_REPORT_ANNUAL_NOTICE_MARKERS, get_effective_filings, get_filing_chunks, get_filings
from app.services.retrieval_service import RetrievalService
from app.utils.metadata_utils import infer_market_from_ticker

logger = get_logger(__name__)

REQUIRED_TOPICS = {
    "business_model": "Core business operations and business model",
    "competition": "Competitive landscape, competitors, and market share",
    "management": "Management team, board of directors, and governance structure",
    "compensation": "Compensation, equity incentives, and executive pay",
    "risk_factors": "Risk factors, uncertainties, and regulatory risks",
    "segments": "Business segments, segment information, and revenue breakdown",
}

TOPIC_QUERIES = {
    "business_model": "business overview, operations, products and services, 主营业务, 经营模式, 主要产品",
    "competition": "competitive landscape, competitors, market share, 竞争格局, 竞争对手, 市场份额",
    "management": "management team, board of directors, governance, 管理层, 董事会, 公司治理",
    "compensation": "executive compensation, stock awards, equity incentive, 薪酬, 股权激励, 董监高报酬",
    "risk_factors": "risk factors, uncertainties, regulatory risks, litigation, 风险因素, 风险提示, 监管风险, 诉讼",
    "segments": "business segments, segment revenue, revenue breakdown, 分部信息, 分产品, 分地区, 营业收入构成",
}

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "business_model": [
        "item 1. business",
        "item 1.business",
        "our business",
        "business overview",
        "our company",
        "主营业务",
        "经营模式",
        "主要业务",
        "主要产品",
        "产品和服务",
        "公司从事",
    ],
    "competition": [
        "competition",
        "competitive",
        "competitors",
        "market share",
        "竞争",
        "竞争格局",
        "竞争对手",
        "市场份额",
        "行业地位",
        "同行业",
    ],
    "management": [
        "management",
        "board of directors",
        "executive officers",
        "governance",
        "管理层",
        "董事会",
        "高级管理人员",
        "公司治理",
        "治理结构",
    ],
    "compensation": [
        "compensation",
        "stock awards",
        "equity incentive",
        "executive pay",
        "薪酬",
        "报酬",
        "股权激励",
        "董事、监事、高级管理人员",
        "董监高",
    ],
    "risk_factors": [
        "risk factor",
        "uncertainties",
        "regulatory risk",
        "litigation",
        "风险因素",
        "风险提示",
        "不确定性",
        "监管风险",
        "诉讼",
        "重大风险",
    ],
    "segments": [
        "segment",
        "segment revenue",
        "segment information",
        "revenue breakdown",
        "分部信息",
        "分产品",
        "分地区",
        "营业收入构成",
        "主营业务分行业",
        "主营业务分产品",
    ],
}

MIN_EVIDENCE_SCORE = 0.05
MIN_EVIDENCE_COUNT = 1


class FilingCoverageService:
    """Check whether local filing evidence covers required analysis topics."""

    def __init__(self, retrieval_service: RetrievalService | None = None) -> None:
        self.retrieval_service = retrieval_service

    def _keyword_fallback(self, session: Session, company_id: int, topic: str) -> list[dict[str, Any]]:
        """Fall back to SQL keyword search when vector search scores are too low."""

        keywords = TOPIC_KEYWORDS.get(topic, [])
        if not keywords:
            return []

        conditions = [func.lower(FilingChunk.text).contains(kw.lower()) for kw in keywords]
        notice_conditions = [func.lower(Filing.title).contains(marker.lower()) for marker in NON_REPORT_ANNUAL_NOTICE_MARKERS]
        stmt = (
            select(FilingChunk)
            .join(Filing, Filing.id == FilingChunk.filing_id)
            .where(FilingChunk.company_id == company_id)
            .where(Filing.filing_type == "annual_report")
            .where(Filing.parse_status == "parsed")
            .where(or_(*conditions))
            .limit(3)
        )
        if notice_conditions:
            stmt = stmt.where(not_(or_(*notice_conditions)))
        rows = session.scalars(stmt).all()
        return [
            {"filing_id": r.filing_id, "section": r.section, "score": 1.0, "text": r.text[:200]}
            for r in rows
        ]

    def _repair_downloaded_filings_before_check(self, session: Session, company: Any) -> None:
        """Self-heal stale downloaded filings before declaring coverage missing."""

        filings = get_filings(session, company.id)
        effective_filings = get_effective_filings(session, company.id)
        existing_chunks = get_filing_chunks(session, company.id)
        has_notice_pollution = any(
            filing.local_path
            and filing.download_status == "downloaded"
            and filing.title
            and ("annual report" in filing.title.lower() or "年度报告" in filing.title)
            and any(marker in filing.title.lower() for marker in NON_REPORT_ANNUAL_NOTICE_MARKERS)
            for filing in filings
        )
        if existing_chunks and effective_filings and not has_notice_pollution:
            return
        local_downloaded = [
            filing
            for filing in filings
            if filing.download_status == "downloaded" and filing.local_path
        ]
        if not local_downloaded:
            return

        from app.services.filing_service import FilingService

        logger.info(
            "filing_coverage_repair_attempt",
            ticker=company.ticker,
            local_downloaded_count=len(local_downloaded),
        )
        FilingService().ingest_company_filings(
            session,
            company,
            market=infer_market_from_ticker(company.ticker),
            filing_types={"annual_report"},
        )
        session.flush()

    def check(self, session: Session, ticker: str, company_profile: dict[str, Any]) -> dict[str, Any]:
        """Return coverage status per topic with warnings for gaps."""

        company = get_company_by_ticker(session, ticker)
        if not company:
            return {
                "covered": {},
                "missing": list(REQUIRED_TOPICS.keys()),
                "warnings": [f"Company record not found: {ticker}"],
                "overall_coverage": 0.0,
            }

        if self.retrieval_service is None:
            self.retrieval_service = RetrievalService()

        self._repair_downloaded_filings_before_check(session, company)

        covered: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        warnings: list[str] = []

        for topic, query in TOPIC_QUERIES.items():
            hits = self.retrieval_service.search(
                query=query,
                filters={"company_id": company.id, "filing_type": "annual_report"},
                top_k=3,
            )
            qualified = [h for h in hits if float(h.get("score") or 0) >= MIN_EVIDENCE_SCORE]

            if len(qualified) < MIN_EVIDENCE_COUNT:
                fallback_hits = self._keyword_fallback(session, company.id, topic)
                if fallback_hits:
                    logger.info("filing_coverage_keyword_fallback", topic=topic, hit_count=len(fallback_hits))
                    qualified = fallback_hits

            if len(qualified) >= MIN_EVIDENCE_COUNT:
                covered[topic] = {
                    "hit_count": len(qualified),
                    "top_score": max(float(h.get("score") or 0) for h in qualified),
                    "evidence_refs": [
                        {"filing_id": h.get("filing_id"), "section": h.get("section"), "score": h.get("score")}
                        for h in qualified[:3]
                    ],
                }
            else:
                missing.append(topic)
                label = REQUIRED_TOPICS[topic]
                warnings.append(f"Insufficient filing evidence: {label} ({topic}) — subsequent analysis may be limited.")

        total = len(REQUIRED_TOPICS)
        overall = len(covered) / total if total > 0 else 0.0

        return {
            "covered": covered,
            "missing": missing,
            "warnings": warnings,
            "overall_coverage": round(overall, 2),
        }
