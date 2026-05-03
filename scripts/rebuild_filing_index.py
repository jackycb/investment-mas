"""Rebuild filing txt artifacts, DB chunks, and Chroma vectors from local files."""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import select

from app.core.database import SessionLocal
from app.db.models import Company
from app.db.repositories.company_repository import get_company_by_ticker
from app.services.filing_service import FilingService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild local annual-report index from downloaded filings.")
    parser.add_argument("--ticker", action="append", help="Ticker to rebuild. Can be passed multiple times.")
    parser.add_argument("--all", action="store_true", help="Rebuild every company with downloaded filings.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.ticker and not args.all:
        raise SystemExit("Pass --ticker TICKER or --all.")

    session = SessionLocal()
    try:
        if args.all:
            companies = list(session.scalars(select(Company).order_by(Company.ticker.asc())).all())
        else:
            companies = []
            for ticker in args.ticker:
                company = get_company_by_ticker(session, ticker)
                if company is None:
                    print(json.dumps({"ticker": ticker, "error": "company_not_found"}, ensure_ascii=False))
                    continue
                companies.append(company)

        service = FilingService()
        results: list[dict[str, Any]] = []
        for company in companies:
            result = service.rebuild_local_filing_index(session, company, filing_types={"annual_report"})
            session.commit()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False))

        total = {
            "company_count": len(results),
            "processed": sum(int(item["processed"]) for item in results),
            "skipped": sum(int(item["skipped"]) for item in results),
            "chunk_count": sum(int(item["chunk_count"]) for item in results),
        }
        print(json.dumps({"summary": total}, ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
