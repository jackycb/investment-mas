"""Test helper data loaders."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Company, CompanyProfile, FinancialReport, NewsArticle, PeerMapping


def load_demo_dataset(session: Session) -> None:
    """Load a compact dataset into the test database."""

    now = datetime.now(timezone.utc)
    nvda = Company(ticker="US.NVDA", company_name="NVIDIA", created_at=now, updated_at=now)
    amd = Company(ticker="US.AMD", company_name="AMD", created_at=now, updated_at=now)
    avgo = Company(ticker="US.AVGO", company_name="Broadcom", created_at=now, updated_at=now)
    session.add_all([nvda, amd, avgo])
    session.flush()

    session.add(
        CompanyProfile(
            company_id=nvda.id,
            as_of_date=date.today(),
            business_model="Accelerated computing platform.",
            market_space="AI infrastructure.",
            competition_landscape="Strong but competitive.",
            corporate_culture="Engineering-led.",
            revenue_model="Hardware plus software.",
            key_products=["GPU"],
            key_regions=["US"],
            key_customers=["Cloud"],
            key_suppliers=["TSMC"],
            management_summary="Disciplined execution.",
            raw_sources=[{"source": "tests"}],
            created_at=now,
        )
    )
    session.add_all(
        [
            PeerMapping(company_id=nvda.id, peer_company_id=amd.id, peer_type="core", similarity_score=0.9, rationale="Direct", source_type="test", created_at=now),
            PeerMapping(company_id=nvda.id, peer_company_id=avgo.id, peer_type="valuation", similarity_score=0.8, rationale="Comp", source_type="test", created_at=now),
        ]
    )
    for company, values in {
        nvda: [(2020, 10, 0.60, 0.20, 0.20, 3.0), (2021, 15, 0.62, 0.22, 0.24, 4.2), (2022, 20, 0.64, 0.24, 0.28, 5.5), (2023, 25, 0.61, 0.21, 0.23, 5.1), (2024, 30, 0.66, 0.28, 0.31, 8.2)],
        amd: [(2020, 8, 0.43, 0.11, 0.15, 0.6), (2021, 10, 0.44, 0.12, 0.16, 0.8), (2022, 12, 0.45, 0.10, 0.14, 0.6), (2023, 13, 0.46, 0.08, 0.10, 0.7), (2024, 14, 0.47, 0.09, 0.12, 0.9)],
        avgo: [(2020, 20, 0.73, 0.30, 0.32, 7.0), (2021, 22, 0.74, 0.31, 0.33, 7.8), (2022, 25, 0.75, 0.32, 0.34, 8.2), (2023, 27, 0.75, 0.33, 0.35, 9.0), (2024, 30, 0.76, 0.34, 0.36, 9.8)],
    }.items():
        for fiscal_year, revenue, gross_margin, net_margin, roe, fcf in values:
            net_income = revenue * net_margin
            session.add(
                FinancialReport(
                    company_id=company.id,
                    period_type="annual",
                    fiscal_year=fiscal_year,
                    fiscal_period="FY",
                    report_date=date(fiscal_year, 12, 31),
                    revenue=revenue,
                    gross_profit=revenue * gross_margin,
                    operating_profit=revenue * (net_margin + 0.08),
                    net_income=net_income,
                    operating_cash_flow=net_income * 1.05,
                    free_cash_flow=fcf,
                    total_assets=revenue * 2.0,
                    total_liabilities=revenue * 0.8,
                    total_equity=revenue * 1.2,
                    shares_outstanding=2.5,
                    gross_margin=gross_margin,
                    net_margin=net_margin,
                    roe=roe,
                    roa=roe / 2,
                    debt_ratio=0.40,
                    raw_source={"source": "tests"},
                    created_at=now,
                )
            )
    session.add(
        NewsArticle(
            company_id=nvda.id,
            title="NVIDIA faces margin pressure as supply mix shifts",
            source_name="Test News",
            source_type="finance_news",
            url="https://example.com",
            published_at=now,
            author="Tester",
            content="Short content",
            summary="Margin pressure remains a watch item.",
            language="en",
            raw_html=None,
            fetched_at=now,
        )
    )
    session.commit()

