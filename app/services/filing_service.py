"""End-to-end filing ingestion service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Company, Filing, FilingChunk
from app.db.repositories.filing_repository import (
    SEARCH_PAGE_SOURCE_NAMES,
    SEARCH_PAGE_URL_MARKERS,
    delete_filing_chunks,
    filing_tables_ready,
    get_filing_by_checksum,
    get_filing_by_file_url,
    get_filing_chunks_by_filing,
    get_filings,
)
from app.providers.filing_discovery_provider import FilingDiscoveryProvider, MarketFilingDiscoveryProvider
from app.providers.filing_download_provider import FilingDownloadProvider, HttpFilingDownloadProvider
from app.providers.vector_store_provider import VectorStoreProvider, build_default_vector_store
from app.services.chunking_service import ChunkingService
from app.services.document_parser_service import DocumentParserService
from app.services.embedding_service import EmbeddingService
from app.utils.metadata_utils import infer_market_from_ticker, json_safe

logger = get_logger(__name__)
settings = get_settings()


class FilingService:
    """Discover, download, parse, chunk, and index filings for one company."""

    def __init__(
        self,
        discovery_provider: FilingDiscoveryProvider | None = None,
        download_provider: FilingDownloadProvider | None = None,
        parser_service: DocumentParserService | None = None,
        chunking_service: ChunkingService | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_store: VectorStoreProvider | None = None,
    ) -> None:
        self.discovery_provider = discovery_provider or MarketFilingDiscoveryProvider()
        self.download_provider = download_provider or HttpFilingDownloadProvider()
        self.parser_service = parser_service or DocumentParserService()
        self.chunking_service = chunking_service or ChunkingService()
        self.embedding_service = embedding_service or EmbeddingService()
        self.vector_store = vector_store

    def ingest_company_filings(
        self,
        session: Session,
        company: Company,
        market: str | None = None,
        years: int | None = None,
        filing_types: set[str] | None = None,
    ) -> tuple[list[Filing], list[str]]:
        """Ingest discoverable filings and return stored filing rows plus warnings."""

        if not settings.enable_filing_ingestion:
            return get_filings(session, company.id), ["ENABLE_FILING_INGESTION is disabled."]
        if not filing_tables_ready(session):
            warning = "filings / filing_chunks tables have not been created; filing ingestion skipped. Please run alembic upgrade head."
            logger.warning("filing_tables_missing", ticker=company.ticker)
            return [], [warning]

        normalized_market = (market or infer_market_from_ticker(company.ticker)).upper()
        lookback_years = years or settings.default_filing_lookback_years
        warnings: list[str] = []
        self._repair_existing_downloaded_filings(session, company, filing_types, warnings)
        filings_meta = self.discovery_provider.discover_filings(company.ticker, company.company_name, normalized_market, years=lookback_years)
        if filing_types:
            filings_meta = [item for item in filings_meta if str(item.get("filing_type") or "") in filing_types]
        logger.info("filing_discovery_completed", ticker=company.ticker, market=normalized_market, filing_count=len(filings_meta))

        for item in filings_meta:
            self._ingest_one_filing(session, company, normalized_market, item, warnings)

        return get_filings(session, company.id), warnings

    def rebuild_local_filing_index(
        self,
        session: Session,
        company: Company,
        filing_types: set[str] | None = None,
    ) -> dict[str, Any]:
        """Rebuild txt, DB chunks, and vector index strictly from local downloaded filings."""

        if not filing_tables_ready(session):
            raise RuntimeError("filings / filing_chunks tables are missing; run alembic upgrade head first.")

        warnings: list[str] = []
        processed = 0
        skipped = 0
        chunk_count = 0
        for filing in get_filings(session, company.id):
            if self._is_search_or_stub_filing(filing):
                self._mark_non_report_filing_ignored(session, filing, "search_page_or_stub")
                skipped += 1
                continue
            if self._is_non_report_annual_notice(str(filing.title or "")):
                self._mark_non_report_annual_notice(session, filing)
                skipped += 1
                continue
            if filing_types and filing.filing_type not in filing_types:
                skipped += 1
                continue
            if filing.download_status != "downloaded" or not filing.local_path:
                skipped += 1
                continue

            delete_filing_chunks(session, filing.id)
            if self._reparse_existing_local_filing(session, company, filing, warnings):
                processed += 1
                chunk_count += int((filing.metadata_json or {}).get("chunk_count") or 0)
            else:
                filing.parse_status = "failed"
                filing.metadata_json = json_safe({**(filing.metadata_json or {}), "parse_error": "empty_or_failed"})
                warnings.append(f"Local filing parse failed: {filing.file_url}")
                skipped += 1

        session.flush()
        logger.info(
            "local_filing_index_rebuild_completed",
            ticker=company.ticker,
            processed=processed,
            skipped=skipped,
            chunk_count=chunk_count,
            warning_count=len(warnings),
        )
        return {
            "ticker": company.ticker,
            "processed": processed,
            "skipped": skipped,
            "chunk_count": chunk_count,
            "warnings": warnings,
        }

    def _repair_existing_downloaded_filings(
        self,
        session: Session,
        company: Company,
        filing_types: set[str] | None,
        warnings: list[str],
    ) -> None:
        """Reparse already downloaded local filings that have no chunks yet."""

        for filing in get_filings(session, company.id):
            if self._is_search_or_stub_filing(filing):
                self._mark_non_report_filing_ignored(session, filing, "search_page_or_stub")
                continue
            if self._is_non_report_annual_notice(str(filing.title or "")):
                self._mark_non_report_annual_notice(session, filing)
                continue
            if filing_types and filing.filing_type not in filing_types:
                continue
            if filing.download_status != "downloaded" or not filing.local_path:
                continue
            if get_filing_chunks_by_filing(session, filing.id):
                self._ensure_text_for_existing_parsed_filing(session, filing)
                continue
            if self._reparse_existing_local_filing(session, company, filing, warnings):
                logger.info("filing_existing_failed_repaired", ticker=company.ticker, filing_id=filing.id, fiscal_year=filing.fiscal_year)

    def _ingest_one_filing(
        self,
        session: Session,
        company: Company,
        market: str,
        filing_meta: dict[str, Any],
        warnings: list[str],
    ) -> None:
        if self._is_non_report_annual_notice(str(filing_meta.get("title") or "")):
            logger.info(
                "filing_non_report_annual_notice_skipped",
                ticker=company.ticker,
                title=filing_meta.get("title"),
                file_url=filing_meta.get("file_url"),
            )
            return
        if bool((filing_meta.get("metadata_json") or {}).get("stub")):
            logger.info(
                "filing_stub_candidate_skipped",
                ticker=company.ticker,
                source_name=filing_meta.get("source_name"),
                file_url=filing_meta.get("file_url"),
            )
            return

        file_url = str(filing_meta.get("file_url") or "")
        existing = get_filing_by_file_url(session, company.id, file_url) or self._find_existing_filing_by_url(session, company.id, file_url)
        if existing and existing.local_path and get_filing_chunks_by_filing(session, existing.id):
            self._ensure_text_for_existing_parsed_filing(session, existing)
            return
        if existing and existing.local_path and existing.download_status == "downloaded":
            if self._reparse_existing_local_filing(session, company, existing, warnings):
                return

        downloaded = self.download_provider.download_filing({**filing_meta, "market": market, "ticker": company.ticker, "company_name": company.company_name})
        if downloaded.get("status") != "downloaded":
            warnings.append(f"Filing download failed: {filing_meta.get('file_url') or filing_meta.get('source_url')}")
            filing = existing or self._upsert_filing_row(session, company, market, filing_meta, download_status="failed")
            filing.metadata_json = json_safe({**(filing.metadata_json or {}), "download_error": downloaded.get("error")})
            session.flush()
            return

        duplicate = get_filing_by_checksum(session, company.id, str(downloaded.get("checksum") or ""))
        if duplicate and duplicate.id != getattr(existing, "id", None):
            if self._is_non_report_annual_notice(str(duplicate.title or "")):
                self._mark_non_report_annual_notice(session, duplicate)
                return
            if duplicate.local_path and not get_filing_chunks_by_filing(session, duplicate.id):
                self._reparse_existing_local_filing(session, company, duplicate, warnings)
            elif duplicate.local_path:
                self._ensure_text_for_existing_parsed_filing(session, duplicate)
            return

        filing = self._upsert_filing_row(
            session,
            company,
            market,
            downloaded,
            download_status="downloaded",
            parse_status="pending",
        )
        parsed, parsed_path = self._parse_downloaded_payload(downloaded)
        if not parsed:
            filing.parse_status = "failed"
            filing.metadata_json = json_safe({**(filing.metadata_json or {}), "parse_error": "empty_or_failed"})
            warnings.append(f"Filing parsing failed or no text extracted: {filing.file_url}")
            session.flush()
            return
        text_local_path = self._ensure_text_local_artifact(filing, parsed_path, parsed)

        delete_filing_chunks(session, filing.id)
        base_metadata = {
            "ticker": company.ticker,
            "company_name": company.company_name,
            "market": market,
            "filing_type": filing.filing_type,
            "fiscal_year": filing.fiscal_year,
        }
        chunks = self.chunking_service.build_chunks(parsed, base_metadata)
        logger.info("filing_chunking_completed", filing_id=filing.id, chunk_count=len(chunks), ticker=company.ticker)
        embeddings = self.embedding_service.embed_texts([item["text"] for item in chunks]) if chunks else []
        logger.info("filing_embedding_completed", filing_id=filing.id, chunk_count=len(chunks), ticker=company.ticker)
        vector_payload: list[dict[str, Any]] = []
        for chunk, embedding in zip(chunks, embeddings, strict=False):
            row = FilingChunk(
                filing_id=filing.id,
                company_id=company.id,
                chunk_index=int(chunk["chunk_index"]),
                section=chunk.get("section"),
                page_number=chunk.get("page_number"),
                text=str(chunk["text"]),
                token_count=chunk.get("token_count"),
                embedding_model=settings.default_embedding_provider,
                embedding_status="indexed",
                metadata_json=json_safe(chunk.get("metadata_json") or {}),
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            vector_payload.append(
                {
                    "vector_id": f"{filing.id}:{row.chunk_index}",
                    "text": row.text,
                    "embedding": embedding,
                    "metadata": {
                        "filing_id": filing.id,
                        "company_id": company.id,
                        "ticker": company.ticker,
                        "company_name": company.company_name,
                        "market": market,
                        "filing_type": filing.filing_type,
                        "fiscal_year": filing.fiscal_year or 0,
                        "section": row.section or "",
                        "page_number": row.page_number or 0,
                    },
                }
            )
        if self.vector_store is None:
            self.vector_store = build_default_vector_store()
        logger.info("filing_vector_upsert_started", filing_id=filing.id, chunk_count=len(vector_payload), ticker=company.ticker)
        self.vector_store.upsert_chunks(vector_payload)
        logger.info("filing_vector_upsert_completed", filing_id=filing.id, chunk_count=len(vector_payload), ticker=company.ticker)
        filing.parse_status = "parsed"
        filing.metadata_json = json_safe(
            {
                **(filing.metadata_json or {}),
                "chunk_count": len(chunks),
                "text_local_path": str(text_local_path) if text_local_path else (filing.metadata_json or {}).get("text_local_path"),
                "parsed_from_path": str(parsed_path),
            }
        )
        logger.info("filing_ingestion_completed", filing_id=filing.id, chunk_count=len(chunks), ticker=company.ticker)
        session.flush()

    def _reparse_existing_local_filing(
        self,
        session: Session,
        company: Company,
        filing: Filing,
        warnings: list[str],
    ) -> bool:
        """Retry parsing from an already downloaded local filing without re-downloading."""

        parsed, parse_path = self._parse_existing_filing_candidates(filing)
        if not parsed or parse_path is None:
            return False
        text_local_path = self._ensure_text_local_artifact(filing, parse_path, parsed)

        delete_filing_chunks(session, filing.id)
        base_metadata = {
            "ticker": company.ticker,
            "company_name": company.company_name,
            "market": filing.market,
            "filing_type": filing.filing_type,
            "fiscal_year": filing.fiscal_year,
        }
        chunks = self.chunking_service.build_chunks(parsed, base_metadata)
        logger.info("filing_chunking_completed", filing_id=filing.id, chunk_count=len(chunks), ticker=company.ticker)
        embeddings = self.embedding_service.embed_texts([item["text"] for item in chunks]) if chunks else []
        logger.info("filing_embedding_completed", filing_id=filing.id, chunk_count=len(chunks), ticker=company.ticker)
        vector_payload: list[dict[str, Any]] = []
        for chunk, embedding in zip(chunks, embeddings, strict=False):
            row = FilingChunk(
                filing_id=filing.id,
                company_id=company.id,
                chunk_index=int(chunk["chunk_index"]),
                section=chunk.get("section"),
                page_number=chunk.get("page_number"),
                text=str(chunk["text"]),
                token_count=chunk.get("token_count"),
                embedding_model=settings.default_embedding_provider,
                embedding_status="indexed",
                metadata_json=json_safe(chunk.get("metadata_json") or {}),
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            vector_payload.append(
                {
                    "vector_id": f"{filing.id}:{row.chunk_index}",
                    "text": row.text,
                    "embedding": embedding,
                    "metadata": {
                        "filing_id": filing.id,
                        "company_id": company.id,
                        "ticker": company.ticker,
                        "company_name": company.company_name,
                        "market": filing.market,
                        "filing_type": filing.filing_type,
                        "fiscal_year": filing.fiscal_year or 0,
                        "section": row.section or "",
                        "page_number": row.page_number or 0,
                    },
                }
            )
        if self.vector_store is None:
            self.vector_store = build_default_vector_store()
        logger.info("filing_reparse_vector_upsert_started", filing_id=filing.id, chunk_count=len(vector_payload), ticker=company.ticker)
        self.vector_store.upsert_chunks(vector_payload)
        logger.info("filing_reparse_vector_upsert_completed", filing_id=filing.id, chunk_count=len(vector_payload), ticker=company.ticker)
        filing.parse_status = "parsed"
        filing.metadata_json = json_safe(
            {
                **(filing.metadata_json or {}),
                "chunk_count": len(chunks),
                "reparsed_from_local": True,
                "text_local_path": str(text_local_path) if text_local_path else (filing.metadata_json or {}).get("text_local_path"),
                "parsed_from_path": str(parse_path),
            }
        )
        logger.info("filing_reparse_completed", filing_id=filing.id, chunk_count=len(chunks), ticker=company.ticker, parse_path=str(parse_path))
        session.flush()
        return True

    def _is_non_report_annual_notice(self, title: str) -> bool:
        """Return whether a HKEX/CNINFO result is only a publication notice, not an annual report."""

        normalized = " ".join(title.lower().replace("\n", " ").split())
        if "annual report" not in normalized and "年度报告" not in normalized:
            return False
        non_report_markers = (
            "notification letter",
            "reply form",
            "notice of publication",
            "non-registered holder",
            "non-registered shareholder",
            "registered holder",
            "registered shareholder",
            "electronic dissemination",
            "arrangement of electronic dissemination",
            "发布通知",
            "通知信函",
            "回条",
            "非登记",
            "登记股东",
            "以电子方式发布",
        )
        return any(marker in normalized for marker in non_report_markers)

    def _mark_non_report_annual_notice(self, session: Session, filing: Filing) -> None:
        """Demote previously stored annual-report publication notices so retrieval ignores them."""

        self._mark_non_report_filing_ignored(session, filing, "annual_report_publication_notice")

    def _mark_non_report_filing_ignored(self, session: Session, filing: Filing, reason: str) -> None:
        """Mark a persisted non-report filing as ignored and remove any stale chunks."""

        delete_filing_chunks(session, filing.id)
        metadata = filing.metadata_json or {}
        filing.filing_type = "ir_material"
        filing.parse_status = "ignored"
        filing.metadata_json = json_safe({**metadata, "ignored_reason": reason})
        logger.info("filing_non_report_ignored", filing_id=filing.id, ticker=filing.ticker, reason=reason, title=filing.title)
        session.flush()

    def _is_search_or_stub_filing(self, filing: Filing) -> bool:
        """Return whether a stored row is a search page/stub rather than a disclosure file."""

        metadata = filing.metadata_json or {}
        file_url = str(filing.file_url or "")
        if metadata.get("stub"):
            return True
        if filing.source_name == "sec_edgar_direct_filing" and str(filing.ticker or "").upper() not in {"PDD", "US.PDD"}:
            return True
        if filing.source_name in SEARCH_PAGE_SOURCE_NAMES:
            return True
        return any(marker in file_url for marker in SEARCH_PAGE_URL_MARKERS)

    def _parse_downloaded_payload(self, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], Path]:
        """Try every local artifact saved for a download until one yields text."""

        for path, file_type in self._downloaded_parse_candidates(payload):
            parsed = self.parser_service.parse(str(path), file_type)
            if parsed:
                logger.info("filing_parse_candidate_succeeded", parse_path=str(path), file_type=file_type)
                return parsed, path
            logger.warning("filing_parse_candidate_empty", parse_path=str(path), file_type=file_type)
        local_path = Path(str(payload.get("local_path") or ""))
        return [], local_path

    def _parse_existing_filing_candidates(self, filing: Filing) -> tuple[list[dict[str, Any]], Path | None]:
        """Try every known local artifact for an existing filing until one yields text."""

        for path, file_type in self._existing_parse_candidates(filing):
            parsed = self.parser_service.parse(str(path), file_type)
            if parsed:
                logger.info("filing_existing_parse_candidate_succeeded", filing_id=filing.id, parse_path=str(path), file_type=file_type)
                return parsed, path
            logger.warning("filing_existing_parse_candidate_empty", filing_id=filing.id, parse_path=str(path), file_type=file_type)
        return [], None

    def _upsert_filing_row(
        self,
        session: Session,
        company: Company,
        market: str,
        payload: dict[str, Any],
        download_status: str,
        parse_status: str = "pending",
    ) -> Filing:
        metadata = self._merged_filing_metadata(payload)
        filing = get_filing_by_file_url(session, company.id, str(payload.get("file_url") or ""))
        if filing is None:
            filing = Filing(
                company_id=company.id,
                ticker=company.ticker,
                company_name=company.company_name,
                market=market,
                filing_type=str(payload.get("filing_type") or "other"),
                period_type=payload.get("period_type"),
                fiscal_year=payload.get("fiscal_year"),
                fiscal_period=payload.get("fiscal_period"),
                report_date=self._coerce_date(payload.get("report_date")),
                published_at=self._coerce_datetime(payload.get("published_at")),
                title=str(payload.get("title") or "")[:500] or None,
                source_name=payload.get("source_name"),
                source_url=payload.get("source_url"),
                file_url=str(payload.get("file_url") or payload.get("source_url") or ""),
                local_path=payload.get("local_path"),
                file_type=payload.get("file_type"),
                checksum=payload.get("checksum"),
                file_size=payload.get("file_size"),
                mime_type=payload.get("mime_type"),
                download_status=download_status,
                parse_status=parse_status,
                metadata_json=json_safe(metadata),
            )
            session.add(filing)
        else:
            filing.local_path = payload.get("local_path") or filing.local_path
            filing.file_type = payload.get("file_type") or filing.file_type
            filing.checksum = payload.get("checksum") or filing.checksum
            filing.file_size = payload.get("file_size") or filing.file_size
            filing.mime_type = payload.get("mime_type") or filing.mime_type
            filing.download_status = download_status
            filing.parse_status = parse_status
            filing.metadata_json = json_safe(metadata)
        session.flush()
        return filing

    def _merged_filing_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Keep provider metadata and storage artifacts in one DB JSON payload."""

        metadata = dict(payload.get("metadata_json") or {})
        for key in (
            "raw_local_path",
            "text_local_path",
            "raw_file_type",
            "stored_file_type",
            "stored_suffix",
            "checksum",
            "mime_type",
            "source_name",
            "source_url",
            "file_url",
        ):
            if payload.get(key) is not None:
                metadata[key] = payload.get(key)
        return metadata

    def _coerce_date(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day") and not isinstance(value, str):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            except ValueError:
                return None
        return None

    def _coerce_datetime(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _find_existing_filing_by_url(self, session: Session, company_id: int, file_url: str) -> Filing | None:
        """Find an existing filing by URL using a tolerant in-memory fallback."""

        normalized = self._normalize_file_url(file_url)
        if not normalized:
            return None
        for filing in get_filings(session, company_id):
            if self._normalize_file_url(str(filing.file_url or "")) == normalized:
                return filing
        return None

    def _normalize_file_url(self, value: str) -> str:
        """Normalize filing URLs for duplicate detection."""

        return value.strip().lower()

    def _downloaded_parse_candidates(self, payload: dict[str, Any]) -> list[tuple[Path, str]]:
        """Return ordered parse candidates from freshly downloaded storage metadata."""

        metadata = dict(payload.get("metadata_json") or {})
        metadata.update({key: value for key, value in payload.items() if key.endswith("_local_path") or key.endswith("_file_type")})
        return self._parse_candidates_from_values(
            local_path=payload.get("local_path"),
            file_type=payload.get("file_type"),
            metadata=metadata,
        )

    def _existing_parse_candidates(self, filing: Filing) -> list[tuple[Path, str]]:
        """Return ordered parse candidates for a persisted filing, reading sidecar metadata if needed."""

        metadata = dict(filing.metadata_json or {})
        metadata.update(self._read_sidecar_metadata(filing.local_path))
        return self._parse_candidates_from_values(
            local_path=filing.local_path,
            file_type=filing.file_type,
            metadata=metadata,
        )

    def _parse_candidates_from_values(
        self,
        local_path: Any,
        file_type: Any,
        metadata: dict[str, Any],
    ) -> list[tuple[Path, str]]:
        """Build a de-duplicated list of local text/raw artifacts to parse."""

        raw_file_type = str(metadata.get("raw_file_type") or "").lower()
        raw_path = metadata.get("raw_local_path")
        text_path = metadata.get("text_local_path")

        raw_type = raw_file_type or self._infer_path_type(str(raw_path or ""))
        entries = [
            (text_path, "txt"),
            (local_path, str(file_type or "").lower()),
            (raw_path, raw_type),
        ]

        candidates: list[tuple[Path, str]] = []
        seen: set[str] = set()
        for value, candidate_type in entries:
            if not value:
                continue
            path = Path(str(value))
            if not path.exists():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append((path, candidate_type or self._infer_path_type(str(path))))
        return candidates

    def _read_sidecar_metadata(self, local_path: str | None) -> dict[str, Any]:
        """Read local sidecar metadata so old DB rows can recover raw/text paths."""

        if not local_path:
            return {}
        path = Path(str(local_path))
        sidecar = path.with_suffix(path.suffix + ".meta.json")
        if not sidecar.exists():
            return {}
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.info("filing_sidecar_metadata_read_failed", local_path=str(local_path), error=str(exc))
            return {}
        return payload if isinstance(payload, dict) else {}

    def _best_local_parse_type(self, filing: Filing, path: Path) -> str:
        """Infer parse type from the chosen local path."""

        return self._infer_path_type(str(path)) or str(filing.file_type or "").lower()

    def _infer_path_type(self, value: str) -> str:
        """Infer a parser file type from a path or URL."""

        suffix = Path(value).suffix.lower().lstrip(".")
        if suffix in {"pdf", "txt", "html", "htm"}:
            return "html" if suffix == "htm" else suffix
        return ""

    def _ensure_text_local_artifact(self, filing: Filing, parse_path: Path, parsed: list[dict[str, Any]]) -> Path | None:
        """Persist a txt companion for reparsed filings when one does not already exist."""

        metadata = filing.metadata_json or {}
        existing_text = metadata.get("text_local_path")
        if existing_text:
            existing_path = Path(str(existing_text))
            if existing_path.exists():
                return existing_path

        if parse_path.suffix.lower() == ".txt":
            return parse_path

        text_payload = "\n\n".join(str(item.get("text", "")).strip() for item in parsed if str(item.get("text", "")).strip()).strip()
        if not text_payload:
            return None

        text_path = Path(str(filing.local_path)).with_suffix(".txt") if filing.local_path else parse_path.with_suffix(".txt")
        text_path.write_text(text_payload, encoding="utf-8")
        logger.info("filing_text_artifact_created", filing_id=filing.id, text_local_path=str(text_path))
        return text_path

    def _ensure_text_for_existing_parsed_filing(self, session: Session, filing: Filing) -> None:
        """Backfill txt artifact for already parsed filings that predate txt persistence."""

        metadata = filing.metadata_json or {}
        if metadata.get("text_local_path"):
            text_path = Path(str(metadata.get("text_local_path")))
            if text_path.exists():
                return
        if not filing.local_path or str(filing.local_path).lower().endswith(".txt"):
            return

        chunks = get_filing_chunks_by_filing(session, filing.id)
        if not chunks:
            return
        text_payload = "\n\n".join(chunk.text.strip() for chunk in chunks if str(chunk.text).strip()).strip()
        if not text_payload:
            return
        text_path = Path(str(filing.local_path)).with_suffix(".txt")
        text_path.write_text(text_payload, encoding="utf-8")
        filing.metadata_json = json_safe({**metadata, "text_local_path": str(text_path)})
        logger.info("filing_text_backfilled", filing_id=filing.id, text_local_path=str(text_path))
        session.flush()
