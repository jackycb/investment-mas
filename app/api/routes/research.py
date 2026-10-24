"""Research endpoints."""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db_session
from app.db.repositories.research_repository import get_latest_report_by_ticker
from app.db.repositories.company_repository import get_company_by_ticker
from app.db.repositories.document_repository import (
    get_company_document_by_id,
    get_company_documents,
    get_document_chunks_by_document,
)
from app.db.repositories.filing_repository import get_effective_filings, get_filing_chunks_by_filing
from app.schemas.document import (
    CompanyDocumentChunkListResponse,
    CompanyDocumentChunkSchema,
    CompanyDocumentListResponse,
    CompanyDocumentSchema,
)
from app.schemas.filing import FilingListResponse, FilingSchema
from app.schemas.research import (
    FeedbackRequest,
    FeedbackResponse,
    FollowupQARequest,
    FollowupQAResponse,
    LatestResearchResponse,
    PerformanceHistoryResponse,
    PrewarmRequest,
    PrewarmResponse,
    RefreshPeersRequest,
    RefreshPeersResponse,
    RerunSectionRequest,
    RerunSectionResponse,
    ResearchChatRequest,
    ResearchChatResponse,
    ResearchRunRequest,
    ResearchRunResponse,
    RunPerformanceDetailResponse,
)
from app.services import performance_tracker
from app.services.feedback_refinement_service import FeedbackRefinementService
from app.services.followup_qa_service import FollowupQAService
from app.services.docx_export_service import markdown_report_to_docx
from app.services.research_runner import ResearchRunner
from app.services.aliyun_llm_service import get_llm_service
from app.utils.metadata_utils import normalize_ticker

router = APIRouter(prefix="/research", tags=["research"])


@router.post("/run", response_model=ResearchRunResponse)
def run_research(payload: ResearchRunRequest, session: Session = Depends(get_db_session)) -> ResearchRunResponse:
    """Run the research workflow and persist outputs."""

    execution = ResearchRunner(session, model=payload.model).run(
        payload.ticker, payload.company_name, force_refresh=payload.force_refresh
    )
    return execution.response


@router.get("/stream")
def stream_research(
    ticker: str,
    company_name: str,
    model: str | None = None,
    force_refresh: bool = False,
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    """Run the research workflow and stream progress via SSE."""

    def event_stream() -> Iterator[str]:
        def emit(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        try:
            runner = ResearchRunner(session, model=model)
            for item in runner.run_stream(ticker, company_name, force_refresh=force_refresh):
                yield emit(item["event"], item["data"])
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("stream_research_failed: %s", exc, exc_info=True)
            session.rollback()
            yield emit("error", {"detail": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{ticker}/latest", response_model=LatestResearchResponse)
def get_latest_research(ticker: str, session: Session = Depends(get_db_session)) -> LatestResearchResponse:
    """Return the latest report for a ticker."""

    ticker = normalize_ticker(ticker)
    report = get_latest_report_by_ticker(session, ticker)
    if not report or not report.report_json:
        raise HTTPException(status_code=404, detail=f"No report found for ticker {ticker.upper()}")
    return LatestResearchResponse(
        ticker=ticker.upper(),
        company_name=report.company.company_name,
        final_rating=report.final_rating,
        executive_summary=report.executive_summary or "",
        report_markdown=report.report_markdown,
        report_json=report.report_json,
    )


@router.get("/{ticker}/plan")
def get_research_plan(ticker: str, session: Session = Depends(get_db_session)) -> dict:
    """Return the research plan embedded in the latest report."""

    ticker = normalize_ticker(ticker)
    report = get_latest_report_by_ticker(session, ticker)
    if not report or not report.report_json:
        raise HTTPException(status_code=404, detail=f"No report found for ticker {ticker.upper()}")
    plan = report.report_json.get("_research_plan")
    if not plan:
        raise HTTPException(status_code=404, detail=f"No research plan found for ticker {ticker.upper()}")
    return {"ticker": ticker.upper(), "research_plan": plan}
@router.get("/{ticker}/performance", response_model=PerformanceHistoryResponse)
def get_performance_history(
    ticker: str, limit: int = 10, session: Session = Depends(get_db_session)
) -> PerformanceHistoryResponse:
    """Return measured per-run telemetry for a ticker (optimization task §10).

    ``comparison`` diffs the newest run against the oldest loaded run so the
    repeat-run saving is a measured number, not an estimate.
    """

    ticker = normalize_ticker(ticker)
    bounded = max(1, min(int(limit or 10), 50))
    data = performance_tracker.ticker_performance(session, ticker, limit=bounded)
    return PerformanceHistoryResponse(**data)


@router.get("/{ticker}/performance/{task_id}", response_model=RunPerformanceDetailResponse)
def get_performance_run(
    ticker: str, task_id: str, session: Session = Depends(get_db_session)
) -> RunPerformanceDetailResponse:
    """Return span-level detail (nodes, skills, LLM calls) for one run."""

    detail = performance_tracker.load_run_detail(session, task_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"No telemetry found for task {task_id}")
    detail.setdefault("ticker", normalize_ticker(ticker).upper())
    return RunPerformanceDetailResponse(**detail)





@router.get("/{ticker}/report.docx")
def download_report_docx(ticker: str, session: Session = Depends(get_db_session)) -> StreamingResponse:
    """Download the latest report as a Word document."""

    ticker = normalize_ticker(ticker)
    report = get_latest_report_by_ticker(session, ticker)
    if not report or not report.report_markdown:
        raise HTTPException(status_code=404, detail=f"No report found for ticker {ticker}")
    buf = markdown_report_to_docx(report.report_markdown, report.report_json or {})
    filename = f"{ticker.replace('.', '_')}_report.docx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{ticker}/documents", response_model=CompanyDocumentListResponse)
def get_company_documents_view(ticker: str, session: Session = Depends(get_db_session)) -> CompanyDocumentListResponse:
    """Return stored profiling documents for a ticker."""

    ticker = normalize_ticker(ticker)
    company = get_company_by_ticker(session, ticker)
    if not company:
        raise HTTPException(status_code=404, detail=f"No company found for ticker {ticker.upper()}")
    documents = get_company_documents(session, company.id)
    return CompanyDocumentListResponse(
        ticker=company.ticker,
        company_name=company.company_name,
        documents=[
            CompanyDocumentSchema(
                id=item.id,
                ticker=item.ticker,
                company_name=item.company_name,
                doc_type=item.doc_type,
                fiscal_year=item.fiscal_year,
                report_date=item.report_date.isoformat() if item.report_date else None,
                source_name=item.source_name,
                source_url=item.source_url,
                local_path=item.local_path,
                checksum=item.checksum,
                downloaded_at=item.downloaded_at.isoformat(),
                chunk_count=len(get_document_chunks_by_document(session, item.id)),
                raw_metadata=item.raw_metadata or {},
            )
            for item in documents
        ],
    )


@router.get("/{ticker}/documents/{document_id}/chunks", response_model=CompanyDocumentChunkListResponse)
def get_company_document_chunks_view(
    ticker: str,
    document_id: int,
    session: Session = Depends(get_db_session),
) -> CompanyDocumentChunkListResponse:
    """Return parsed chunks for one stored company document."""

    ticker = normalize_ticker(ticker)
    company = get_company_by_ticker(session, ticker)
    if not company:
        raise HTTPException(status_code=404, detail=f"No company found for ticker {ticker.upper()}")
    document = get_company_document_by_id(session, document_id)
    if not document or document.company_id != company.id:
        raise HTTPException(status_code=404, detail=f"No document {document_id} found for ticker {ticker.upper()}")
    chunks = get_document_chunks_by_document(session, document_id)
    return CompanyDocumentChunkListResponse(
        ticker=company.ticker,
        company_name=company.company_name,
        document_id=document_id,
        chunks=[
            CompanyDocumentChunkSchema(
                id=item.id,
                section=item.section,
                page_number=item.page_number,
                chunk_index=item.chunk_index,
                text=item.text,
                doc_type=item.doc_type,
                fiscal_year=item.fiscal_year,
                ticker=item.ticker,
                company_name=item.company_name,
                chunk_metadata=item.chunk_metadata or {},
            )
            for item in chunks
        ],
    )


@router.get("/{ticker}/filings", response_model=FilingListResponse)
def get_company_filings_view(ticker: str, session: Session = Depends(get_db_session)) -> FilingListResponse:
    """Return effective stored filings for a ticker."""

    ticker = normalize_ticker(ticker)
    company = get_company_by_ticker(session, ticker)
    if not company:
        raise HTTPException(status_code=404, detail=f"No company found for ticker {ticker.upper()}")
    filings = get_effective_filings(session, company.id)
    return FilingListResponse(
        ticker=company.ticker,
        company_name=company.company_name,
        filings=[
            FilingSchema(
                id=item.id,
                ticker=item.ticker,
                company_name=item.company_name,
                market=item.market,
                filing_type=item.filing_type,
                fiscal_year=item.fiscal_year,
                report_date=item.report_date.isoformat() if item.report_date else None,
                published_at=item.published_at.isoformat() if item.published_at else None,
                title=item.title,
                source_name=item.source_name,
                source_url=item.source_url,
                file_url=item.file_url,
                local_path=item.local_path,
                raw_local_path=(item.metadata_json or {}).get("raw_local_path"),
                file_type=item.file_type,
                download_status=item.download_status,
                parse_status=item.parse_status,
                chunk_count=len(get_filing_chunks_by_filing(session, item.id)),
                metadata_json=item.metadata_json or {},
            )
            for item in filings
        ],
    )


@router.post("/chat", response_model=ResearchChatResponse)
def chat_about_research(payload: ResearchChatRequest, session: Session = Depends(get_db_session)) -> ResearchChatResponse:
    """Answer follow-up questions grounded in the latest saved report."""

    payload.ticker = normalize_ticker(payload.ticker)
    report = get_latest_report_by_ticker(session, payload.ticker)
    if not report or not report.report_json:
        raise HTTPException(status_code=404, detail=f"No report found for ticker {payload.ticker.upper()}")

    report_json = report.report_json
    llm = get_llm_service()
    answer = ""
    if llm.available:
        try:
            content = llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are an investment research assistant. "
                            "You may only answer based on the research report JSON provided by the user; do not fabricate new facts. "
                            "Answer in English, concisely and directly. "
                            'If the report does not contain the answer, explicitly say "Insufficient report information".'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "ticker": payload.ticker.upper(),
                                "question": payload.message,
                                "report_json": report_json,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.1,
            )
            answer = (content or "").strip()
        except Exception:
            answer = ""

    if not answer:
        executive_summary = str(report_json.get("executive_summary") or report.executive_summary or "")
        final_rating = str(report_json.get("final_rating_label") or report.final_rating or "")
        answer = executive_summary or f"The latest rating for {payload.ticker.upper()} is {final_rating or 'research_more'}."

    return ResearchChatResponse(ticker=payload.ticker.upper(), answer=answer)


@router.post("/followup", response_model=FollowupQAResponse)
def followup_qa(payload: FollowupQARequest, session: Session = Depends(get_db_session)) -> FollowupQAResponse:
    """Answer follow-up questions with evidence from filing chunks and report context."""

    ticker = normalize_ticker(payload.ticker)
    result = FollowupQAService().answer(
        session=session,
        ticker=ticker,
        question=payload.question,
    )
    return FollowupQAResponse(
        ticker=ticker,
        answer=result.get("answer", ""),
        evidence_refs=result.get("evidence_refs", []),
        answer_confidence=result.get("answer_confidence", 0.0),
        unresolved_questions=result.get("unresolved_questions", []),
    )


@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(payload: FeedbackRequest, session: Session = Depends(get_db_session)) -> FeedbackResponse:
    """Submit feedback to refine a specific report section."""

    ticker = normalize_ticker(payload.ticker)
    result = FeedbackRefinementService().refine(
        session=session,
        ticker=ticker,
        section_name=payload.section_name,
        feedback_type=payload.feedback_type,
        feedback_text=payload.feedback_text,
    )
    return FeedbackResponse(
        ticker=ticker,
        updated_section=result.get("updated_section", payload.section_name),
        impacted_sections=result.get("impacted_sections", []),
        refined_content=result.get("refined_content", ""),
        additional_evidence_count=result.get("additional_evidence_count", 0),
        feedback_resolution=result.get("feedback_resolution", ""),
    )


@router.post("/prewarm", response_model=PrewarmResponse)
def prewarm_filings(payload: PrewarmRequest, session: Session = Depends(get_db_session)) -> PrewarmResponse:
    """Trigger filing ingestion without running the full research workflow."""

    from app.db.repositories.company_repository import get_or_create_company
    from app.services.filing_service import FilingService
    from app.utils.metadata_utils import infer_market_from_ticker

    ticker = normalize_ticker(payload.ticker)
    company = get_or_create_company(session, ticker, payload.company_name)
    service = FilingService()
    try:
        filings, warnings = service.ingest_company_filings(
            session, company,
            market=infer_market_from_ticker(ticker),
            filing_types={"annual_report"},
        )
        session.commit()
        return PrewarmResponse(
            ticker=ticker,
            filings_ingested=len(filings),
            warnings=warnings,
            status="completed",
        )
    except Exception as exc:
        session.rollback()
        return PrewarmResponse(ticker=ticker, warnings=[str(exc)], status="failed")


@router.post("/refresh-peers", response_model=RefreshPeersResponse)
def refresh_peers(payload: RefreshPeersRequest, session: Session = Depends(get_db_session)) -> RefreshPeersResponse:
    """Refresh peer candidates and load metric snapshots."""

    from app.services.peer_intelligence_service import PeerIntelligenceService

    ticker = normalize_ticker(payload.ticker)
    company = get_company_by_ticker(session, ticker)
    profile: dict = {}
    if company:
        from app.db.repositories.company_repository import get_latest_company_profile
        stored = get_latest_company_profile(session, company.id)
        if stored:
            profile = {
                "business_model": stored.business_model or "",
                "market_space": stored.market_space or "",
                "competition_landscape": stored.competition_landscape or "",
                "key_products": stored.key_products or [],
            }

    service = PeerIntelligenceService()
    candidates, warnings = service.discover_candidates(
        ticker=ticker,
        company_name=payload.company_name,
        company_profile=profile,
    )
    peer_tickers = [str(p.get("ticker", "")) for p in candidates if p.get("ticker")]
    metrics = service.load_peer_metrics(peer_tickers)
    return RefreshPeersResponse(
        ticker=ticker,
        peer_count=len(candidates),
        peers_with_metrics=len(metrics),
        peer_candidates=candidates[:8],
        warnings=warnings,
    )


@router.post("/rerun-section", response_model=RerunSectionResponse)
def rerun_section(payload: RerunSectionRequest, session: Session = Depends(get_db_session)) -> RerunSectionResponse:
    """Rerun a single report section without full workflow re-execution."""

    ticker = normalize_ticker(payload.ticker)
    result = FeedbackRefinementService().refine(
        session=session,
        ticker=ticker,
        section_name=payload.section_name,
        feedback_type="need_more_detail",
        feedback_text=payload.feedback_text or f"Please regenerate the {payload.section_name} section with deeper analysis.",
    )
    return RerunSectionResponse(
        ticker=ticker,
        section_name=payload.section_name,
        refined_content=result.get("refined_content", ""),
        impacted_sections=result.get("impacted_sections", []),
    )
