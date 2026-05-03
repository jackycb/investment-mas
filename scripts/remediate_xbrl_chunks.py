"""Re-parse US EDGAR .txt filings that contain XBRL garbage in their DB chunks.

Usage (dry-run first):
    python scripts/remediate_xbrl_chunks.py --dry-run
    python scripts/remediate_xbrl_chunks.py
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add the project root to sys.path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Filing, FilingChunk
from app.db.repositories.filing_repository import (
    delete_filing_chunks,
    get_effective_filings,
)
from app.services.chunking_service import ChunkingService
from app.services.embedding_service import EmbeddingService
from app.providers.vector_store_provider import build_default_vector_store
from app.utils.metadata_utils import json_safe
from app.utils.text_cleaner import strip_xbrl_header

logger = get_logger(__name__)
settings = get_settings()


def filing_needs_remediation(filing: Filing) -> bool:
    """Check whether a filing likely contains XBRL header garbage."""
    if not filing.local_path or not filing.local_path.endswith(".txt"):
        return False
    if filing.market and filing.market.upper() != "US":
        return False
    local_path = Path(filing.local_path)
    if not local_path.exists():
        logger.warning("local_path_missing", filing_id=filing.id, path=str(local_path))
        return False
    try:
        raw = local_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return len(strip_xbrl_header(raw)) < len(raw)


def remediate_filing(
    session: Any,
    filing: Filing,
    chunking_service: ChunkingService,
    embedding_service: EmbeddingService,
    vector_store: Any,
    dry_run: bool = False,
) -> int:
    """Re-parse, re-chunk, and re-index a single filing. Returns number of new chunks."""
    from app.utils.text_cleaner import clean_text

    local_path = Path(filing.local_path)
    if not local_path.exists():
        logger.warning("local_path_missing_skip", filing_id=filing.id)
        return 0

    text = clean_text(local_path.read_text(encoding="utf-8", errors="ignore"))
    if not text:
        logger.warning("empty_after_clean", filing_id=filing.id)
        return 0

    parsed = [{"page_number": None, "section": "Document", "text": text}]
    base_metadata = {
        "ticker": filing.ticker,
        "company_name": filing.company_name,
        "market": filing.market or "US",
        "filing_type": filing.filing_type,
        "fiscal_year": filing.fiscal_year,
    }

    chunks = chunking_service.build_chunks(parsed, base_metadata)
    if dry_run:
        logger.info(
            "dry_run_would_remediate",
            filing_id=filing.id,
            ticker=filing.ticker,
            fiscal_year=filing.fiscal_year,
            new_chunk_count=len(chunks),
            local_path=str(local_path),
        )
        return len(chunks)

    # Delete old chunks and re-create
    delete_filing_chunks(session, filing.id)
    session.flush()

    texts = [c["text"] for c in chunks]
    embeddings = embedding_service.embed_texts(texts) if texts else []

    vector_payload: list[dict[str, Any]] = []
    for chunk, embedding in zip(chunks, embeddings, strict=False):
        row = FilingChunk(
            filing_id=filing.id,
            company_id=filing.company_id,
            chunk_index=int(chunk["chunk_index"]),
            section=chunk.get("section"),
            page_number=chunk.get("page_number"),
            text=str(chunk["text"]),
            token_count=int(chunk.get("token_count", 0)),
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
                    "company_id": filing.company_id,
                    "ticker": filing.ticker,
                    "company_name": filing.company_name,
                    "market": filing.market or "US",
                    "filing_type": filing.filing_type,
                    "fiscal_year": filing.fiscal_year or 0,
                    "section": row.section or "",
                    "page_number": row.page_number or 0,
                },
            }
        )

    if vector_store:
        vector_store.upsert_chunks(vector_payload)

    filing.parse_status = "parsed"
    filing.metadata_json = json_safe({**(filing.metadata_json or {}), "chunk_count": len(chunks), "xbrl_remediated": True})
    session.flush()

    logger.info("remediated_filing", filing_id=filing.id, ticker=filing.ticker, new_chunk_count=len(chunks))
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remediate XBRL-header garbage in US filing chunks")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be done")
    parser.add_argument("--ticker", type=str, help="Limit to one ticker (e.g., NVDA)")
    parser.add_argument("--filing-id", type=int, help="Limit to a single filing ID")
    args = parser.parse_args()

    session = SessionLocal()
    chunking_service = ChunkingService()
    embedding_service = EmbeddingService()
    vector_store = build_default_vector_store()

    try:
        # Find all US companies with filings
        rows = session.execute(
            text("SELECT DISTINCT company_id FROM filings WHERE market = 'US' AND parse_status = 'parsed' AND local_path LIKE '%.txt'")
        ).fetchall()
        company_ids = [r[0] for r in rows]

        total_remediated = 0
        total_filings_checked = 0

        for cid in company_ids:
            filings = get_effective_filings(session, cid)
            for filing in filings:
                if args.filing_id and filing.id != args.filing_id:
                    continue
                if args.ticker:
                    target = args.ticker.upper()
                    ticker_upper = (filing.ticker or "").upper()
                    # Accept both "NVDA" and "US.NVDA" style tickers
                    if ticker_upper != target and not ticker_upper.endswith("." + target):
                        continue
                total_filings_checked += 1
                if not filing_needs_remediation(filing):
                    continue
                chunks = remediate_filing(session, filing, chunking_service, embedding_service, vector_store, dry_run=args.dry_run)
                total_remediated += chunks
                session.commit()

        action = "Would remediate" if args.dry_run else "Remediated"
        logger.info(
            "remediation_summary",
            action=action,
            filings_checked=total_filings_checked,
            total_new_chunks=total_remediated,
        )
        print(f"{action} {total_remediated} chunks across {total_filings_checked} filings checked.")
    except Exception as exc:
        session.rollback()
        logger.error("remediation_failed", error=str(exc))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()