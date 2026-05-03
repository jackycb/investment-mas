"""Research workflow state definitions."""

from typing import TypedDict

from app.providers.company_info_provider import get_canonical_company_name
from app.utils.metadata_utils import normalize_ticker


class ResearchState(TypedDict, total=False):
    """Shared state flowing through the research workflow."""

    ticker: str
    company_name: str
    company_profile: dict
    peer_list: list[dict]
    filing_coverage: dict
    business_analysis: dict
    segment_analysis: dict
    culture_analysis: dict
    peer_analysis: dict
    company_news: list[dict]
    peer_news: list[dict]
    event_list: list[dict]
    financial_summary: dict
    peer_comparison: dict
    financial_red_flags: list[str]
    valuation_result: dict
    valuation_assumptions: dict
    risk_assessment: dict
    report_coverage: dict
    final_report: str
    final_rating: str
    report_json: dict
    errors: list[str]
    warnings: list[str]


def create_initial_state(ticker: str, company_name: str) -> ResearchState:
    """Create an empty workflow state."""

    ticker = normalize_ticker(ticker)
    canonical_company_name = get_canonical_company_name(ticker, company_name)
    return ResearchState(
        ticker=ticker,
        company_name=canonical_company_name,
        company_profile={},
        peer_list=[],
        filing_coverage={},
        business_analysis={},
        segment_analysis={},
        culture_analysis={},
        peer_analysis={},
        company_news=[],
        peer_news=[],
        event_list=[],
        financial_summary={},
        peer_comparison={},
        financial_red_flags=[],
        valuation_result={},
        valuation_assumptions={},
        risk_assessment={},
        report_coverage={},
        final_report="",
        final_rating="research_more",
        report_json={},
        errors=[],
        warnings=[],
    )
