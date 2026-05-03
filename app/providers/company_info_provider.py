"""Company information provider interfaces and lightweight implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.aliyun_llm_service import get_llm_service

logger = get_logger(__name__)
settings = get_settings()

CANONICAL_COMPANY_NAMES = {
    "HK.00883": "CNOOC",
    "PDD": "PDD Holdings",
    "US.PDD": "PDD Holdings",
    "NVDA": "NVIDIA",
    "US.NVDA": "NVIDIA",
    "META": "Meta Platforms",
    "US.META": "Meta Platforms",
    "TSM": "Taiwan Semiconductor Manufacturing",
    "US.TSM": "Taiwan Semiconductor Manufacturing",
}


def get_canonical_company_name(ticker: str, company_name: str) -> str:
    """Return a canonical company name for well-known tickers, overriding obvious typos."""

    normalized_ticker = ticker.strip().upper()
    canonical = CANONICAL_COMPANY_NAMES.get(normalized_ticker)
    if canonical:
        return canonical
    if normalized_ticker.startswith("US.") and company_name.strip().upper() == normalized_ticker.split(".", 1)[1]:
        return normalized_ticker.split(".", 1)[1]
    return company_name.strip()


CURATED_COMPANY_PROFILES = {
    "HK.00883": {
        "business_model": "CNOOC focuses on offshore oil and gas exploration, development, production, and sales. Profitability is driven by production volume, oil prices, and per-barrel cost control.",
        "market_space": "Core market is offshore oil and gas development in China and overseas, heavily influenced by global energy prices, capex cycles, and national energy security needs.",
        "competition_landscape": "Primarily benchmarked against PetroChina, Sinopec, and international integrated energy companies; advantages lie in offshore asset quality, cost control, and dividend capability.",
        "corporate_culture": "Emphasizes prudent operations, capital discipline, and shareholder returns with a pragmatic decision-making style.",
        "revenue_model": "Revenue from crude oil, natural gas, and related product sales; cash flows are highly correlated with commodity price fluctuations.",
        "key_products": ["Crude oil", "Natural gas", "LNG"],
        "key_regions": ["China offshore", "South America", "Africa", "North Sea"],
        "key_customers": ["Refineries", "Natural gas buyers", "International energy traders"],
        "key_suppliers": ["Offshore engineering service providers", "Oilfield service companies", "Equipment suppliers"],
        "risk_factors": ["Oil and gas price volatility", "Capex cycle risk", "Geopolitical and regulatory changes"],
        "management_summary": "Management focuses on reserve growth, cost control, capex discipline, and stable dividends.",
    },
    "US.PDD": {
        "business_model": "PDD Holdings connects merchants and consumers through Pinduoduo and Temu platforms, monetizing via transaction commissions, marketing services, and platform ecosystem.",
        "market_space": "Covers China e-commerce and cross-border value retail markets; growth driven by lower-tier market penetration, global expansion, and supply chain efficiency.",
        "competition_landscape": "Key competitors include Alibaba, JD.com, Douyin e-commerce, SHEIN, and Amazon; competition centers on traffic, fulfillment, subsidies, and merchant ecosystem.",
        "corporate_culture": "Emphasizes execution efficiency, algorithm-driven operations, and high-frequency iteration with fast strategic advancement.",
        "revenue_model": "Revenue primarily from online marketing services, transaction services, and cross-border platform related income.",
        "key_products": ["Pinduoduo", "Temu", "Merchant marketing tools"],
        "key_regions": ["China", "North America", "Europe", "Asia Pacific"],
        "key_customers": ["Platform merchants", "Online consumers", "Cross-border sellers"],
        "key_suppliers": ["Platform merchants", "Logistics partners", "Payment and technology service providers"],
        "risk_factors": ["Intensifying competition", "Cross-border regulatory changes", "Subsidy investment volatility"],
        "management_summary": "Management focuses on user growth, merchant ecosystem, internationalization, and balancing investment returns.",
    },
}

CURATED_PEERS = {
    "NVDA": [
        {"ticker": "AMD", "company_name": "Advanced Micro Devices", "peer_type": "core", "similarity_score": 0.92},
        {"ticker": "AVGO", "company_name": "Broadcom", "peer_type": "valuation", "similarity_score": 0.83},
    ],
    "US.NVDA": [
        {"ticker": "AMD", "company_name": "Advanced Micro Devices", "peer_type": "core", "similarity_score": 0.92},
        {"ticker": "AVGO", "company_name": "Broadcom", "peer_type": "valuation", "similarity_score": 0.83},
    ],
    "HK.00883": [
        {"ticker": "SH.601857", "company_name": "PetroChina", "peer_type": "core", "similarity_score": 0.84},
        {"ticker": "HK.00386", "company_name": "Sinopec", "peer_type": "secondary", "similarity_score": 0.73},
    ],
    "HK.00700": [
        {"ticker": "HK.09888", "company_name": "Baidu", "peer_type": "core", "similarity_score": 0.80},
        {"ticker": "HK.09999", "company_name": "NetEase", "peer_type": "secondary", "similarity_score": 0.72},
    ],
    "HK.09988": [
        {"ticker": "HK.09618", "company_name": "JD.com", "peer_type": "core", "similarity_score": 0.85},
        {"ticker": "HK.03690", "company_name": "Meituan", "peer_type": "secondary", "similarity_score": 0.70},
    ],
    "PDD": [
        {"ticker": "BABA", "company_name": "Alibaba", "peer_type": "core", "similarity_score": 0.83},
        {"ticker": "JD", "company_name": "JD.com", "peer_type": "secondary", "similarity_score": 0.74},
    ],
    "US.PDD": [
        {"ticker": "BABA", "company_name": "Alibaba", "peer_type": "core", "similarity_score": 0.83},
        {"ticker": "JD", "company_name": "JD.com", "peer_type": "secondary", "similarity_score": 0.74},
    ],
    "META": [
        {"ticker": "GOOGL", "company_name": "Alphabet", "peer_type": "core", "similarity_score": 0.88},
        {"ticker": "PINS", "company_name": "Pinterest", "peer_type": "secondary", "similarity_score": 0.61},
    ],
    "US.META": [
        {"ticker": "GOOGL", "company_name": "Alphabet", "peer_type": "core", "similarity_score": 0.88},
        {"ticker": "PINS", "company_name": "Pinterest", "peer_type": "secondary", "similarity_score": 0.61},
    ],
    "TSM": [
        {"ticker": "INTC", "company_name": "Intel", "peer_type": "secondary", "similarity_score": 0.65},
        {"ticker": "AMD", "company_name": "Advanced Micro Devices", "peer_type": "valuation", "similarity_score": 0.60},
    ],
    "US.TSM": [
        {"ticker": "INTC", "company_name": "Intel", "peer_type": "secondary", "similarity_score": 0.65},
        {"ticker": "AMD", "company_name": "Advanced Micro Devices", "peer_type": "valuation", "similarity_score": 0.60},
    ],
}

INDUSTRY_PEER_CANDIDATES = {
    "semiconductor": [
        {"ticker": "AMD", "company_name": "Advanced Micro Devices", "peer_type": "core", "similarity_score": 0.88},
        {"ticker": "AVGO", "company_name": "Broadcom", "peer_type": "valuation", "similarity_score": 0.79},
        {"ticker": "QCOM", "company_name": "Qualcomm", "peer_type": "secondary", "similarity_score": 0.73},
    ],
    "internet_platform": [
        {"ticker": "BABA", "company_name": "Alibaba", "peer_type": "core", "similarity_score": 0.82},
        {"ticker": "JD", "company_name": "JD.com", "peer_type": "secondary", "similarity_score": 0.74},
        {"ticker": "AMZN", "company_name": "Amazon", "peer_type": "valuation", "similarity_score": 0.68},
    ],
    "social_media": [
        {"ticker": "META", "company_name": "Meta Platforms", "peer_type": "core", "similarity_score": 0.82},
        {"ticker": "GOOGL", "company_name": "Alphabet", "peer_type": "valuation", "similarity_score": 0.75},
        {"ticker": "PINS", "company_name": "Pinterest", "peer_type": "secondary", "similarity_score": 0.62},
    ],
    "oil_gas_upstream": [
        {"ticker": "SH.601857", "company_name": "PetroChina", "peer_type": "core", "similarity_score": 0.84},
        {"ticker": "HK.00386", "company_name": "Sinopec", "peer_type": "secondary", "similarity_score": 0.73},
        {"ticker": "XOM", "company_name": "Exxon Mobil", "peer_type": "valuation", "similarity_score": 0.69},
    ],
}

INDUSTRY_KEYWORDS = {
    "semiconductor": ["gpu", "semiconductor", "chip", "compute", "datacenter", "ai accelerator"],
    "internet_platform": ["e-commerce", "marketplace", "retail platform", "consumer platform", "shopping"],
    "social_media": ["social", "messaging", "advertising platform", "digital content", "community"],
    "oil_gas_upstream": ["oil", "gas", "lng", "upstream", "exploration", "production", "offshore"],
}


class CompanyInfoProvider(ABC):
    """Abstract provider for company profiles and peers."""

    @abstractmethod
    def get_company_profile(self, ticker: str, company_name: str) -> dict:
        """Return company profile data."""

    @abstractmethod
    def get_peers(self, ticker: str, company_name: str) -> list[dict]:
        """Return peer company candidates."""


class MockCompanyInfoProvider(CompanyInfoProvider):
    """Mock provider used for offline or seed-friendly execution."""

    def get_company_profile(self, ticker: str, company_name: str) -> dict:
        curated = CURATED_COMPANY_PROFILES.get(ticker.upper())
        if curated:
            return {
                "as_of_date": date.today().isoformat(),
                **curated,
                "raw_sources": [{"source": "curated_fallback", "ticker": ticker.upper()}],
            }
        return {
            "as_of_date": date.today().isoformat(),
            "business_model": f"{company_name} commercializes through product capabilities, ecosystem stickiness, and scale effects.",
            "market_space": "Target market has significant room for growth, but detailed growth drivers require more official disclosure filings.",
            "competition_landscape": "Industry competition is intense, primarily against leading platforms, specialized firms, and new entrants.",
            "corporate_culture": "Insufficient information",
            "revenue_model": "Revenue from product sales, platform service fees, or follow-on service monetization.",
            "key_products": ["Core products", "Platform services"],
            "key_regions": ["North America", "Europe", "Asia Pacific"],
            "key_customers": ["Enterprise clients", "Consumers", "Channel partners"],
            "key_suppliers": ["Manufacturing partners", "Component suppliers"],
            "risk_factors": ["Demand cycle volatility", "Technology iteration pressure", "Supply chain concentration"],
            "management_summary": "Insufficient information",
            "raw_sources": [{"source": "mock_provider", "ticker": ticker.upper()}],
        }

    def get_peers(self, ticker: str, company_name: str) -> list[dict]:
        return CURATED_PEERS.get(ticker.upper(), [])


class WebCompanyInfoProvider(CompanyInfoProvider):
    """Fallback provider that uses curated/static knowledge and optional LLM peer annotation only."""

    def __init__(self, timeout_seconds: float | None = None, user_agent: str | None = None) -> None:
        self.timeout_seconds = timeout_seconds or settings.web_request_timeout_seconds
        self.user_agent = user_agent or settings.web_user_agent
        self.fallback = MockCompanyInfoProvider()
        self.llm = get_llm_service()

    def get_company_profile(self, ticker: str, company_name: str) -> dict:
        curated = CURATED_COMPANY_PROFILES.get(ticker.upper())
        if curated:
            profile = self.fallback.get_company_profile(ticker, company_name)
            profile["raw_sources"] = [{"source": "curated_fallback", "ticker": ticker.upper()}]
            return profile
        return self._build_unavailable_profile(ticker, company_name)

    def get_peers(self, ticker: str, company_name: str) -> list[dict]:
        candidates = self._structured_peer_candidates(ticker, company_name, {})
        if not candidates:
            return self.fallback.get_peers(ticker, company_name)
        if self.llm.available:
            enriched = self._annotate_peer_candidates_with_llm(ticker, company_name, candidates)
            if enriched:
                return enriched
        return candidates

    def _build_unavailable_profile(self, ticker: str, company_name: str) -> dict:
        """Return an explicit unavailable profile instead of fabricated web content."""

        return {
            "as_of_date": date.today().isoformat(),
            "business_model": "",
            "market_space": "",
            "competition_landscape": "",
            "corporate_culture": "",
            "revenue_model": "",
            "key_products": [],
            "key_regions": [],
            "key_customers": [],
            "key_suppliers": [],
            "risk_factors": [],
            "management_summary": "",
            "official_website": None,
            "raw_sources": [
                {
                    "source": "unavailable",
                    "ticker": ticker.upper(),
                    "company_name": company_name,
                }
            ],
        }

    def _structured_peer_candidates(self, ticker: str, company_name: str, summary: dict) -> list[dict]:
        """Retrieve peer candidates from static mappings and rule-based tags."""

        normalized_ticker = ticker.upper()
        curated = CURATED_PEERS.get(normalized_ticker)
        if curated:
            return self._attach_rule_based_rationale(curated, reason=f"Static mapping match for {normalized_ticker}")

        candidates: list[dict] = []
        inferred_tags = self._infer_industry_tags(ticker, company_name, summary)
        for tag in inferred_tags:
            candidates.extend(INDUSTRY_PEER_CANDIDATES.get(tag, []))
        deduped = self._dedupe_peer_candidates(candidates, exclude_ticker=normalized_ticker)
        if not deduped:
            return []
        return self._attach_rule_based_rationale(deduped, reason=f"Industry tag match: {', '.join(inferred_tags)}")

    def _infer_industry_tags(self, ticker: str, company_name: str, summary: dict) -> list[str]:
        """Infer industry tags using ticker hints and lightweight keywords."""

        normalized_ticker = ticker.upper()
        if normalized_ticker in {"NVDA", "US.NVDA", "AMD", "QCOM", "TSM", "US.TSM"}:
            return ["semiconductor"]
        if normalized_ticker in {"PDD", "US.PDD", "BABA", "JD", "HK.09988"}:
            return ["internet_platform"]
        if normalized_ticker in {"US.META", "META", "HK.00700"}:
            return ["social_media"]
        if normalized_ticker in {"HK.00883", "SH.601857", "HK.00386", "XOM"}:
            return ["oil_gas_upstream"]

        haystack = " ".join(
            [
                str(company_name or ""),
                str(summary.get("title", "") or ""),
                str(summary.get("description", "") or ""),
                str(summary.get("extract", "") or ""),
            ]
        ).lower()
        matched: list[str] = []
        for tag, keywords in INDUSTRY_KEYWORDS.items():
            if any(keyword in haystack for keyword in keywords):
                matched.append(tag)
        return matched

    def _dedupe_peer_candidates(self, candidates: list[dict], exclude_ticker: str) -> list[dict]:
        """Dedupe peer candidates while keeping the highest similarity score."""

        best_by_ticker: dict[str, dict] = {}
        for candidate in candidates:
            peer_ticker = str(candidate.get("ticker", "")).upper().strip()
            if not peer_ticker or peer_ticker == exclude_ticker:
                continue
            existing = best_by_ticker.get(peer_ticker)
            if existing is None or float(candidate.get("similarity_score", 0) or 0) > float(existing.get("similarity_score", 0) or 0):
                best_by_ticker[peer_ticker] = {
                    "ticker": peer_ticker,
                    "company_name": candidate.get("company_name") or peer_ticker,
                    "peer_type": candidate.get("peer_type", "core"),
                    "similarity_score": candidate.get("similarity_score", 0.7),
                }
        return list(best_by_ticker.values())[:4]

    def _attach_rule_based_rationale(self, candidates: list[dict], reason: str) -> list[dict]:
        """Attach deterministic rationale for peer selection."""

        return [
            {
                **candidate,
                "rationale": candidate.get("rationale")
                or f"{candidate.get('company_name')} operates in a similar space as the target company and can serve as a {candidate.get('peer_type', 'core')} peer for comparison; source: {reason}",
            }
            for candidate in candidates
        ]

    def _annotate_peer_candidates_with_llm(
        self,
        ticker: str,
        company_name: str,
        candidates: list[dict],
    ) -> list[dict]:
        """Use the LLM only to rewrite explanations for pre-selected candidates."""

        try:
            payload = self.llm.annotate_peer_candidates(
                ticker=ticker,
                company_name=company_name,
                candidates=candidates,
                raw_context={"source": "curated_or_rule_based_candidates"},
            )
        except Exception as exc:
            logger.warning("peer_annotation_llm_failed", ticker=ticker, error=str(exc))
            return candidates
        if not payload:
            return candidates

        candidate_map = {str(item.get("ticker", "")).upper(): item for item in candidates}
        normalized: list[dict] = []
        for item in payload:
            peer_ticker = str(item.get("ticker", "")).upper().strip()
            if peer_ticker not in candidate_map:
                continue
            base = candidate_map[peer_ticker]
            normalized.append(
                {
                    "ticker": peer_ticker,
                    "company_name": item.get("company_name") or base.get("company_name"),
                    "peer_type": item.get("peer_type") or base.get("peer_type"),
                    "similarity_score": item.get("similarity_score") or item.get("confidence") or base.get("similarity_score"),
                    "similarity_dimensions": item.get("similarity_dimensions") or base.get("similarity_dimensions", []),
                    "rationale": item.get("rationale") or base.get("rationale"),
                }
            )
        return normalized or candidates
