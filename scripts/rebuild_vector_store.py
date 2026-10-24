"""Rebuild ChromaDB vector store from PostgreSQL filing_chunks.

Deletes the existing vector store directory and creates a fresh, compact
ChromaDB from the authoritative data in PostgreSQL.  Re-embeds all chunks
using LocalHashEmbeddingProvider (deterministic, no external API).

Usage:
    .venv/bin/python scripts/rebuild_vector_store.py
    .venv/bin/python scripts/rebuild_vector_store.py --batch-size 500
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import get_db_session
from app.providers.embedding_provider import LocalHashEmbeddingProvider

settings = get_settings()
embedder = LocalHashEmbeddingProvider(dimensions=64)

COLLECTION_NAME = "filing_chunks_local_hash_64"


def rebuild(batch_size: int = 500) -> None:
    vector_dir = settings.vector_store_dir
    old_dir = vector_dir + ".old"

    if os.path.exists(old_dir):
        print(f"Removing previous backup: {old_dir}")
        shutil.rmtree(old_dir, ignore_errors=True)

    if os.path.exists(vector_dir):
        print(f"Renaming {vector_dir} → {old_dir}")
        os.rename(vector_dir, old_dir)

    os.makedirs(vector_dir, exist_ok=True)

    import chromadb

    print(f"Creating fresh ChromaDB at {vector_dir}")
    client = chromadb.PersistentClient(path=vector_dir)
    collection = client.get_or_create_collection(COLLECTION_NAME)

    session = next(get_db_session())

    total = session.execute(text("SELECT COUNT(*) FROM filing_chunks")).scalar()
    print(f"Total chunks in PostgreSQL: {total}")

    query = text("""
        SELECT fc.id, fc.filing_id, fc.company_id, fc.chunk_index,
               fc.section, fc.page_number, fc.text,
               f.filing_type, f.fiscal_year, f.market,
               c.ticker, c.company_name
        FROM filing_chunks fc
        JOIN filings f ON fc.filing_id = f.id
        JOIN companies c ON fc.company_id = c.id
        ORDER BY fc.filing_id, fc.chunk_index
    """)

    rows = session.execute(query).fetchall()
    print(f"Fetched {len(rows)} rows from PostgreSQL")

    start = time.time()
    processed = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]

        ids = []
        texts = []
        metadatas = []

        for row in batch:
            vector_id = f"{row.filing_id}:{row.chunk_index}"
            ids.append(vector_id)
            texts.append(row.text or "")
            metadatas.append({
                "filing_id": row.filing_id,
                "company_id": row.company_id,
                "ticker": row.ticker or "",
                "company_name": row.company_name or "",
                "market": row.market or "",
                "filing_type": row.filing_type or "",
                "fiscal_year": row.fiscal_year or 0,
                "section": row.section or "",
                "page_number": row.page_number or 0,
            })

        embeddings = embedder.embed_texts(texts)

        collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        processed += len(batch)
        elapsed = time.time() - start
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"  [{processed}/{total}] {rate:.0f} chunks/s — {elapsed:.1f}s elapsed")

    elapsed = time.time() - start
    final_count = collection.count()
    print(f"\nDone. {final_count} vectors indexed in {elapsed:.1f}s")

    new_size = os.path.getsize(os.path.join(vector_dir, "chroma.sqlite3"))
    print(f"New ChromaDB size: {new_size / 1024 / 1024:.1f} MB")

    if os.path.exists(old_dir):
        old_db = os.path.join(old_dir, "chroma.sqlite3")
        if os.path.exists(old_db):
            old_size = os.path.getsize(old_db)
            print(f"Old ChromaDB size: {old_size / 1024 / 1024:.1f} MB")
            print(f"Reduction: {(1 - new_size / old_size) * 100:.0f}%")

    print(f"\nOld vector store backed up at: {old_dir}")
    print("Once verified, remove it with:")
    print(f"  rm -rf '{old_dir}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild ChromaDB from PostgreSQL filing_chunks")
    parser.add_argument("--batch-size", type=int, default=500, help="Upsert batch size")
    args = parser.parse_args()
    rebuild(batch_size=args.batch_size)
