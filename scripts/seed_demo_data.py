"""Seed demo data and optionally import local financial files into the database."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.db.models import Company, CompanyProfile, FinancialReport, NewsArticle, PeerMapping
from app.providers.financial_data_provider import LocalFinancialDataProvider
from app.utils.financial_parser import parse_financial_file

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


def _get_or_create_company(session, **kwargs) -> Company:
    ticker = kwargs["ticker"].upper()
    company = session.scalar(select(Company).where(Company.ticker == ticker))
    if company:
        for key, value in kwargs.items():
            if value is not None and getattr(company, key, None) in (None, ""):
                setattr(company, key, value)
        company.updated_at = datetime.now(timezone.utc)
        return company

    company = Company(created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), **kwargs)
    session.add(company)
    session.flush()
    return company


def _infer_market_fields(ticker: str) -> tuple[str | None, str | None]:
    prefix = ticker.split(".", 1)[0].upper() if "." in ticker else None
    exchange_map = {
        "US": ("NASDAQ/NYSE", "US"),
        "HK": ("HKEX", "HK"),
        "SH": ("SSE", "CN"),
        "SZ": ("SZSE", "CN"),
        "JP": ("TSE", "JP"),
    }
    return exchange_map.get(prefix, (None, None))


def seed_demo_entities() -> None:
    """Insert base demo companies, profiles, peer mappings, seeded financials, and news."""

    session = SessionLocal()
    now = datetime.now(timezone.utc)
    try:
        nvda = _get_or_create_company(
            session,
            ticker="NVDA",
            company_name="NVIDIA",
            exchange="NASDAQ",
            market="US",
            currency="USD",
            country="USA",
            sector="Technology",
            industry="Semiconductors",
            sub_industry="AI Accelerators",
            official_website="https://www.nvidia.com",
            ir_website="https://investor.nvidia.com",
            description="Accelerated computing and AI platform company.",
        )
        amd = _get_or_create_company(
            session,
            ticker="AMD",
            company_name="Advanced Micro Devices",
            exchange="NASDAQ",
            market="US",
            currency="USD",
            country="USA",
            sector="Technology",
            industry="Semiconductors",
            sub_industry="Compute",
            official_website="https://www.amd.com",
            ir_website="https://ir.amd.com",
            description="High-performance and adaptive computing company.",
        )
        avgo = _get_or_create_company(
            session,
            ticker="AVGO",
            company_name="Broadcom",
            exchange="NASDAQ",
            market="US",
            currency="USD",
            country="USA",
            sector="Technology",
            industry="Semiconductors",
            sub_industry="Infrastructure Software and Chips",
            official_website="https://www.broadcom.com",
            ir_website="https://investors.broadcom.com",
            description="Infrastructure technology company.",
        )

        existing_profile = session.scalar(
            select(CompanyProfile).where(CompanyProfile.company_id == nvda.id).order_by(CompanyProfile.id.desc())
        )
        if existing_profile is None:
            session.add(
                CompanyProfile(
                    company_id=nvda.id,
                    as_of_date=date.today(),
                    business_model="NVIDIA sells accelerated computing hardware, networking, and software platforms tied to AI workloads.",
                    market_space="Large AI infrastructure TAM spanning cloud, enterprise, sovereign AI, and edge.",
                    competition_landscape="NVIDIA leads in training accelerators, while AMD, hyperscaler ASICs, and custom silicon compete in selected workloads.",
                    corporate_culture="Founder-led engineering culture with fast product cadence and ecosystem focus.",
                    revenue_model="Primarily product sales with attach opportunities from software and enterprise support.",
                    key_products=["GPU", "Networking", "CUDA"],
                    key_regions=["US", "China", "Europe"],
                    key_customers=["Hyperscalers", "Enterprises", "Research labs"],
                    key_suppliers=["TSMC", "Memory vendors", "System partners"],
                    management_summary="Management is focused on AI platform breadth, supply execution, and developer lock-in.",
                    raw_sources=[{"source": "seed_demo_data"}],
                    created_at=now,
                )
            )

        for peer_company, peer_type, score, rationale in [
            (amd, "core", 0.92, "Direct accelerator and data center compute competitor."),
            (avgo, "valuation", 0.83, "Comparable scale and margin quality for valuation framing."),
        ]:
            existing_peer = session.scalar(
                select(PeerMapping).where(
                    PeerMapping.company_id == nvda.id,
                    PeerMapping.peer_company_id == peer_company.id,
                    PeerMapping.peer_type == peer_type,
                )
            )
            if existing_peer is None:
                session.add(
                    PeerMapping(
                        company_id=nvda.id,
                        peer_company_id=peer_company.id,
                        peer_type=peer_type,
                        similarity_score=score,
                        rationale=rationale,
                        source_type="seed",
                        created_at=now,
                    )
                )

        financial_seed = {
            "NVDA": [
                (2020, 10.9, 0.62, 0.26, 0.32, 4.8),
                (2021, 16.7, 0.65, 0.27, 0.31, 5.9),
                (2022, 27.0, 0.66, 0.30, 0.34, 8.1),
                (2023, 26.9, 0.56, 0.16, 0.18, 3.8),
                (2024, 60.9, 0.74, 0.49, 0.91, 28.1),
            ],
            "AMD": [
                (2020, 9.8, 0.45, 0.12, 0.18, 0.8),
                (2021, 16.4, 0.48, 0.16, 0.22, 2.0),
                (2022, 23.6, 0.45, 0.06, 0.05, 0.1),
                (2023, 22.7, 0.46, 0.04, 0.03, 0.7),
                (2024, 25.8, 0.50, 0.08, 0.07, 1.2),
            ],
            "AVGO": [
                (2020, 23.9, 0.74, 0.36, 0.31, 8.1),
                (2021, 27.5, 0.75, 0.38, 0.34, 9.4),
                (2022, 33.2, 0.76, 0.39, 0.38, 11.2),
                (2023, 35.8, 0.75, 0.38, 0.41, 12.4),
                (2024, 42.6, 0.76, 0.40, 0.43, 15.0),
            ],
        }
        company_by_ticker = {company.ticker: company for company in [nvda, amd, avgo]}
        for ticker, rows in financial_seed.items():
            company = company_by_ticker[ticker]
            for fiscal_year, revenue, gross_margin, net_margin, roe, free_cash_flow in rows:
                existing = session.scalar(
                    select(FinancialReport).where(
                        FinancialReport.company_id == company.id,
                        FinancialReport.period_type == "annual",
                        FinancialReport.fiscal_year == fiscal_year,
                    )
                )
                if existing is not None:
                    continue
                net_income = revenue * net_margin
                gross_profit = revenue * gross_margin
                operating_cash_flow = net_income * (0.92 if fiscal_year in {2022, 2023} and ticker == "AMD" else 1.08)
                session.add(
                    FinancialReport(
                        company_id=company.id,
                        period_type="annual",
                        fiscal_year=fiscal_year,
                        fiscal_period="FY",
                        report_date=date(fiscal_year, 12, 31),
                        revenue=revenue,
                        gross_profit=gross_profit,
                        operating_profit=revenue * (net_margin + 0.08),
                        net_income=net_income,
                        operating_cash_flow=operating_cash_flow,
                        free_cash_flow=free_cash_flow,
                        total_assets=revenue * 1.8,
                        total_liabilities=revenue * 0.7,
                        total_equity=revenue * 1.1,
                        shares_outstanding=2.5,
                        gross_margin=gross_margin,
                        net_margin=net_margin,
                        roe=roe,
                        roa=roe / 2,
                        debt_ratio=0.39,
                        raw_source={"source": "seed_demo_data"},
                        created_at=now,
                    )
                )

        for title, url, summary, content in [
            (
                "NVIDIA raises AI product roadmap targets after strong demand",
                "https://example.com/nvda/1",
                "Management highlighted strong AI demand and further supply investments.",
                "Demand remained strong across hyperscalers.",
            ),
            (
                "NVIDIA faces margin pressure as supply mix shifts",
                "https://example.com/nvda/2",
                "Analysts flagged margin pressure as customer mix broadened.",
                "Margin pressure emerged in some lower-end deployments.",
            ),
        ]:
            existing_article = session.scalar(
                select(NewsArticle).where(NewsArticle.company_id == nvda.id, NewsArticle.url == url)
            )
            if existing_article is None:
                session.add(
                    NewsArticle(
                        company_id=nvda.id,
                        title=title,
                        source_name="Seed Finance",
                        source_type="finance_news",
                        url=url,
                        published_at=now,
                        author="Seed Bot",
                        content=content,
                        summary=summary,
                        language="en",
                        raw_html=None,
                        fetched_at=now,
                    )
                )

        session.commit()
        logger.info("demo_seed_completed")
    finally:
        session.close()


def import_local_financials(limit: int | None = None) -> None:
    """Import normalized annual financials from the configured local directory."""

    provider = LocalFinancialDataProvider(base_dir=settings.local_financial_base_dir)
    tickers = provider.available_tickers()
    if limit is not None:
        tickers = tickers[:limit]

    session = SessionLocal()
    imported_reports = 0
    imported_companies = 0
    try:
        for ticker in tickers:
            records = provider.load_company_financials(ticker, ticker, years=5)
            if not records:
                continue

            ticker_files = provider.files_for_ticker(ticker)
            parsed_info = parse_financial_file(ticker_files["info"]) if "info" in ticker_files else {"records": [{}]}
            info = parsed_info["records"][0] if parsed_info["records"] else {}
            exchange, market = _infer_market_fields(ticker)
            company_name = info.get("股票名称") or info.get("股票名稱") or records[0].get("company_name") or ticker

            before = session.scalar(select(Company).where(Company.ticker == ticker))
            company = _get_or_create_company(
                session,
                ticker=ticker,
                company_name=str(company_name),
                exchange=exchange,
                market=market,
                currency="USD" if ticker.startswith("US.") else None,
                description="Imported from local financial directory.",
            )
            if before is None:
                imported_companies += 1

            for record in records:
                fiscal_year = record.get("fiscal_year")
                if fiscal_year is None:
                    continue
                existing = session.scalar(
                    select(FinancialReport).where(
                        FinancialReport.company_id == company.id,
                        FinancialReport.period_type == record.get("period_type", "annual"),
                        FinancialReport.fiscal_year == fiscal_year,
                    )
                )
                if existing is not None:
                    continue
                report_date = record.get("report_date")
                session.add(
                    FinancialReport(
                        company_id=company.id,
                        period_type=record.get("period_type", "annual"),
                        fiscal_year=fiscal_year,
                        fiscal_period=record.get("fiscal_period", "FY"),
                        report_date=date.fromisoformat(report_date) if isinstance(report_date, str) else date(fiscal_year, 12, 31),
                        revenue=record.get("revenue"),
                        gross_profit=record.get("gross_profit"),
                        operating_profit=record.get("operating_profit"),
                        net_income=record.get("net_income"),
                        operating_cash_flow=record.get("operating_cash_flow"),
                        free_cash_flow=record.get("free_cash_flow"),
                        total_assets=record.get("total_assets"),
                        total_liabilities=record.get("total_liabilities"),
                        total_equity=record.get("total_equity"),
                        shares_outstanding=record.get("shares_outstanding"),
                        gross_margin=record.get("gross_margin"),
                        net_margin=record.get("net_margin"),
                        roe=record.get("roe"),
                        roa=record.get("roa"),
                        debt_ratio=record.get("debt_ratio"),
                        raw_source=record.get("raw_source"),
                        created_at=datetime.now(timezone.utc),
                    )
                )
                imported_reports += 1

        session.commit()
        logger.info(
            "local_financial_import_completed",
            imported_companies=imported_companies,
            imported_reports=imported_reports,
            scanned_tickers=len(tickers),
            base_dir=settings.local_financial_base_dir,
        )
    finally:
        session.close()


def main() -> None:
    """CLI entrypoint for demo seeding and local financial imports."""

    parser = argparse.ArgumentParser(description="Seed demo data and optionally import local financial files.")
    parser.add_argument("--skip-demo", action="store_true", help="Skip inserting demo companies/news/profile data.")
    parser.add_argument(
        "--skip-local-financials",
        action="store_true",
        help="Skip importing local financial directory data into financial_reports.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of local tickers imported.")
    args = parser.parse_args()

    if not args.skip_demo:
        seed_demo_entities()
    if not args.skip_local_financials:
        import_local_financials(limit=args.limit)


if __name__ == "__main__":
    main()
