"""Multi-source peer candidate generation and metric snapshot pipeline.

Priority order for peer candidates:
1. Structured company tags / known mappings (CURATED_PEERS)
2. Local financial data directory peer files
3. Industry/sector clustering from company profile
4. Annual report competition mentions (supporting evidence only)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.company_info_provider import CURATED_PEERS, WebCompanyInfoProvider
from app.utils.financial_parser import parse_financial_file
from app.utils.file_discovery import discover_financial_files
from app.utils.metadata_utils import normalize_ticker

logger = get_logger(__name__)
settings = get_settings()

SECTOR_PEER_MAP: dict[str, list[str]] = {
    "semiconductors": ["US.NVDA", "US.AMD", "US.INTC", "US.AVGO", "US.QCOM", "US.TXN", "US.MU", "US.MRVL"],
    "cloud_infrastructure": ["US.AMZN", "US.MSFT", "US.GOOGL", "US.CRM", "US.SNOW", "US.NET"],
    "e_commerce": ["US.AMZN", "US.PDD", "US.BABA", "US.JD", "US.MELI", "US.SE", "US.SHOP"],
    "social_media": ["US.META", "US.SNAP", "US.PINS", "US.RDDT"],
    "enterprise_software": ["US.MSFT", "US.CRM", "US.NOW", "US.ORCL", "US.SAP", "US.ADBE"],
    "fintech": ["US.SQ", "US.PYPL", "US.AFRM", "US.SOFI"],
    "ev_auto": ["US.TSLA", "US.RIVN", "US.NIO", "US.LI", "US.XPEV"],
    "consumer_electronics": ["US.AAPL", "US.SONO", "US.LOGI"],
    "internet_china": ["US.BABA", "US.PDD", "US.JD", "US.BIDU", "HK.09988", "HK.09618"],
}

KEYWORD_TO_SECTOR: dict[str, str] = {
    "semiconductor": "semiconductors",
    "gpu": "semiconductors",
    "chip": "semiconductors",
    "ai infrastructure": "semiconductors",
    "cloud": "cloud_infrastructure",
    "saas": "enterprise_software",
    "e-commerce": "e_commerce",
    "ecommerce": "e_commerce",
    "marketplace": "e_commerce",
    "social": "social_media",
    "advertising": "social_media",
    "payment": "fintech",
    "electric vehicle": "ev_auto",
    "ev": "ev_auto",
    "software": "enterprise_software",
    "consumer electronics": "consumer_electronics",
}


class PeerIntelligenceService:
    """Generate peer candidates from multiple sources and load metric snapshots."""

    def __init__(self, financial_base_dir: str | None = None) -> None:
        self.financial_base_dir = Path(financial_base_dir or settings.local_financial_base_dir)

    def discover_top_peers_fast(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any] | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Lightweight peer discovery using only curated + sector clustering (no network).

        Returns up to `limit` peers sorted by similarity score, suitable for
        early-stage filing ingestion before the full peer analysis runs.
        """

        ticker = normalize_ticker(ticker)
        all_candidates: list[dict[str, Any]] = []

        all_candidates.extend(self._from_curated_mappings(ticker))
        if company_profile:
            all_candidates.extend(self._from_sector_clustering(ticker, company_profile))

        if not all_candidates:
            return []

        merged = self._merge_and_rank(ticker, all_candidates)
        return merged[:limit]

    def discover_candidates(
        self,
        ticker: str,
        company_name: str,
        company_profile: dict[str, Any],
        evidence_bundle: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return merged peer candidates from all sources, plus warnings."""

        ticker = normalize_ticker(ticker)
        warnings: list[str] = []
        all_candidates: list[dict[str, Any]] = []

        curated = self._from_curated_mappings(ticker)
        all_candidates.extend(curated)

        sector_peers = self._from_sector_clustering(ticker, company_profile)
        all_candidates.extend(sector_peers)

        web_peers = self._from_web_provider(ticker, company_name)
        all_candidates.extend(web_peers)

        if evidence_bundle:
            filing_peers = self._from_filing_evidence(ticker, evidence_bundle.get("competition", []))
            all_candidates.extend(filing_peers)
            if not filing_peers:
                warnings.append("No peer candidates extracted from annual report competition sections.")

        if not all_candidates:
            warnings.append("No peer candidates identified from any source.")
            return [], warnings

        merged = self._merge_and_rank(ticker, all_candidates)
        source_summary = self._source_summary(merged)
        logger.info("peer_candidates_discovered", ticker=ticker, count=len(merged), sources=source_summary)
        return merged, warnings

    def load_peer_metrics(self, peer_tickers: list[str]) -> dict[str, dict[str, Any]]:
        """Load metric snapshots for each peer from local financial data."""

        snapshots: dict[str, dict[str, Any]] = {}
        index = self._build_financial_index()
        for raw_ticker in peer_tickers:
            ticker = normalize_ticker(raw_ticker)
            candidates = self._ticker_file_candidates(ticker)
            files: dict[str, Path] = {}
            for candidate in candidates:
                files = index.get(candidate, {})
                if files:
                    break
            if not files:
                continue
            metrics = self._extract_latest_metrics(files)
            if metrics:
                snapshots[ticker] = metrics
        logger.info("peer_metrics_loaded", count=len(snapshots), tickers=list(snapshots.keys()))
        return snapshots

    def load_peer_metrics_from_filings(
        self,
        session: Session,
        peer_tickers: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Extract financial metrics from peer annual report filing chunks.

        Falls back to this when local financial data directory has no files for peers.
        Uses keyword search over filing_chunks in the DB.
        """

        from app.db.repositories.company_repository import get_company_by_ticker
        from app.db.repositories.filing_repository import get_filing_chunks

        snapshots: dict[str, dict[str, Any]] = {}
        for raw_ticker in peer_tickers:
            ticker = normalize_ticker(raw_ticker)
            company = get_company_by_ticker(session, ticker)
            if not company:
                continue

            chunks = get_filing_chunks(session, company.id)
            if not chunks:
                continue

            annual_chunks = [
                c for c in chunks
                if (c.metadata_json or {}).get("filing_type") == "annual_report"
            ]
            if not annual_chunks:
                annual_chunks = chunks

            metrics = self._extract_metrics_from_chunks(annual_chunks)
            if metrics:
                metrics["source"] = "filing_chunks"
                snapshots[ticker] = metrics

        logger.info(
            "peer_metrics_from_filings",
            count=len(snapshots),
            tickers=list(snapshots.keys()),
        )
        return snapshots

    def _extract_metrics_from_chunks(self, chunks: list) -> dict[str, Any] | None:
        """Extract financial metrics from filing chunk text using pattern matching."""

        financial_text = ""
        for chunk in chunks:
            section = (chunk.section or "").lower()
            text = chunk.text or ""
            if any(kw in section for kw in (
                "financial", "income", "revenue", "results of operations",
                "selected", "summary", "consolidated", "highlights",
            )):
                financial_text += " " + text
            elif any(kw in text.lower()[:200] for kw in (
                "revenue", "net income", "gross profit", "total revenue",
                "gross margin", "net margin",
            )):
                financial_text += " " + text

        if not financial_text:
            all_text = " ".join(c.text or "" for c in chunks[:50])
            financial_text = all_text

        revenue = self._extract_dollar_amount(financial_text, [
            r"(?:total\s+)?(?:net\s+)?revenue[s]?\s+(?:was|were|of|reached|totaled)?\s*\$?([\d,]+\.?\d*)\s*(million|billion|M|B)",
            r"\$\s*([\d,]+\.?\d*)\s*(million|billion|M|B)\s+(?:in\s+)?(?:total\s+)?(?:net\s+)?revenue",
            r"revenue[s]?[:\s]+\$?\s*([\d,]+\.?\d*)\s*(million|billion|M|B)",
        ])
        net_income = self._extract_dollar_amount(financial_text, [
            r"net\s+income\s+(?:was|of|reached|totaled)?\s*\$?([\d,]+\.?\d*)\s*(million|billion|M|B)",
            r"net\s+income[:\s]+\$?\s*([\d,]+\.?\d*)\s*(million|billion|M|B)",
        ])
        gross_profit = self._extract_dollar_amount(financial_text, [
            r"gross\s+profit\s+(?:was|of|reached|totaled)?\s*\$?([\d,]+\.?\d*)\s*(million|billion|M|B)",
            r"gross\s+profit[:\s]+\$?\s*([\d,]+\.?\d*)\s*(million|billion|M|B)",
        ])

        gross_margin = self._extract_percentage(financial_text, [
            r"gross\s+(?:profit\s+)?margin\s+(?:was|of|reached)?\s*([\d.]+)\s*%",
            r"([\d.]+)\s*%\s+gross\s+(?:profit\s+)?margin",
        ])
        net_margin = self._extract_percentage(financial_text, [
            r"net\s+(?:income\s+)?margin\s+(?:was|of|reached)?\s*([\d.]+)\s*%",
            r"([\d.]+)\s*%\s+net\s+(?:income\s+)?margin",
        ])
        roe = self._extract_percentage(financial_text, [
            r"return\s+on\s+(?:stockholders\'?\s+)?equity\s+(?:was|of)?\s*([\d.]+)\s*%",
            r"ROE\s+(?:was|of)?\s*([\d.]+)\s*%",
        ])
        roic = self._extract_percentage(financial_text, [
            r"return\s+on\s+invested\s+capital\s+(?:was|of)?\s*([\d.]+)\s*%",
            r"ROIC\s+(?:was|of)?\s*([\d.]+)\s*%",
        ])

        if revenue is None and gross_margin is None and net_income is None:
            if gross_profit is None and roe is None:
                return None

        result: dict[str, Any] = {"revenue": revenue}
        if gross_margin is not None:
            result["gross_margin"] = gross_margin / 100.0
        elif revenue and gross_profit:
            result["gross_margin"] = gross_profit / revenue
        if net_margin is not None:
            result["net_margin"] = net_margin / 100.0
        elif revenue and net_income:
            result["net_margin"] = net_income / revenue
        if roe is not None:
            result["roe"] = roe / 100.0
        if roic is not None:
            result["roic"] = roic / 100.0
        if net_income is not None:
            result["net_income"] = net_income

        fiscal_year = self._extract_fiscal_year(financial_text)
        result["fiscal_year"] = fiscal_year

        result["revenue_growth"] = None
        result["free_cash_flow"] = None
        return result

    def _extract_dollar_amount(self, text: str, patterns: list[str]) -> float | None:
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(1).replace(",", "")
                unit = match.group(2).lower()
                try:
                    value = float(raw)
                    if unit in ("billion", "b"):
                        value *= 1000
                    return value
                except ValueError:
                    continue
        return None

    def _extract_percentage(self, text: str, patterns: list[str]) -> float | None:
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None

    def _extract_fiscal_year(self, text: str) -> int | None:
        matches = re.findall(
            r"(?:fiscal\s+(?:year\s+)?|FY\s*|year\s+ended\s+\w+\s+\d{1,2},?\s*)"
            r"(20[12]\d)",
            text,
            re.IGNORECASE,
        )
        if matches:
            return max(int(y) for y in matches)
        year_matches = re.findall(r"\b(202[0-9])\b", text)
        if year_matches:
            return max(int(y) for y in year_matches)
        return None

    def _from_curated_mappings(self, ticker: str) -> list[dict[str, Any]]:
        bare = ticker.split(".")[-1].upper() if "." in ticker else ticker.upper()
        candidates = CURATED_PEERS.get(bare, CURATED_PEERS.get(ticker.upper(), []))
        return [
            {
                "ticker": normalize_ticker(str(p.get("ticker", ""))),
                "company_name": p.get("company_name", ""),
                "peer_type": p.get("peer_type", "core"),
                "similarity_score": float(p.get("similarity_score", 0.8)),
                "source": "curated_mapping",
                "rationale": p.get("rationale", "Known industry peer from curated mappings."),
            }
            for p in candidates
            if str(p.get("ticker", "")).upper() != bare and normalize_ticker(str(p.get("ticker", ""))) != ticker
        ]

    def _from_sector_clustering(self, ticker: str, company_profile: dict[str, Any]) -> list[dict[str, Any]]:
        profile_text = " ".join(
            str(company_profile.get(k, "")) for k in ("business_model", "market_space", "competition_landscape", "key_products")
        ).lower()

        matched_sectors: set[str] = set()
        for keyword, sector in KEYWORD_TO_SECTOR.items():
            if keyword in profile_text:
                matched_sectors.add(sector)

        candidates: list[dict[str, Any]] = []
        for sector in matched_sectors:
            for peer_ticker in SECTOR_PEER_MAP.get(sector, []):
                if peer_ticker == ticker:
                    continue
                candidates.append({
                    "ticker": peer_ticker,
                    "company_name": peer_ticker.split(".")[-1],
                    "peer_type": "secondary",
                    "similarity_score": 0.6,
                    "source": f"sector_clustering:{sector}",
                    "rationale": f"Identified as a {sector.replace('_', ' ')} sector peer based on company profile.",
                })
        return candidates

    def _from_web_provider(self, ticker: str, company_name: str) -> list[dict[str, Any]]:
        try:
            provider = WebCompanyInfoProvider()
            raw = provider.get_peers(ticker, company_name)
            return [
                {
                    "ticker": normalize_ticker(str(p.get("ticker", ""))),
                    "company_name": p.get("company_name", ""),
                    "peer_type": p.get("peer_type", "secondary"),
                    "similarity_score": float(p.get("similarity_score", 0.5)),
                    "source": "web_provider",
                    "rationale": p.get("rationale", "Peer candidate from web company info."),
                }
                for p in raw
                if p.get("ticker")
            ]
        except Exception as exc:
            logger.warning("web_peer_discovery_failed", ticker=ticker, error=str(exc))
            return []

    def _from_filing_evidence(self, ticker: str, competition_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        alias_map: dict[str, dict[str, Any]] = {}
        for _, peers in CURATED_PEERS.items():
            for peer in peers:
                alias_map[str(peer.get("company_name", "")).lower()] = peer
                alias_map[str(peer.get("ticker", "")).lower()] = peer

        found: list[dict[str, Any]] = []
        for chunk in competition_chunks:
            text = str(chunk.get("text", "")).lower()
            for alias, peer in alias_map.items():
                peer_ticker = normalize_ticker(str(peer.get("ticker", "")))
                if alias and alias in text and peer_ticker != ticker:
                    found.append({
                        "ticker": peer_ticker,
                        "company_name": peer.get("company_name", ""),
                        "peer_type": "core",
                        "similarity_score": 0.7,
                        "source": "filing_evidence",
                        "rationale": f"Mentioned in annual report competition section.",
                    })
        return found

    def _merge_and_rank(self, ticker: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        source_priority = {"curated_mapping": 3, "filing_evidence": 2, "sector_clustering": 1, "web_provider": 1}

        for item in candidates:
            t = normalize_ticker(str(item.get("ticker", "")))
            if not t or t == ticker:
                continue
            if t not in merged:
                merged[t] = {**item, "ticker": t, "sources": [item.get("source", "unknown")]}
                continue
            existing = merged[t]
            existing["sources"].append(item.get("source", "unknown"))
            existing["similarity_score"] = max(
                float(existing.get("similarity_score", 0)),
                float(item.get("similarity_score", 0)),
            )
            if source_priority.get(item.get("source", ""), 0) > source_priority.get(existing.get("source", ""), 0):
                existing["peer_type"] = item.get("peer_type", existing["peer_type"])
                existing["rationale"] = item.get("rationale", existing["rationale"])
                existing["source"] = item.get("source", existing["source"])
            if not existing.get("company_name") or existing["company_name"] == t.split(".")[-1]:
                existing["company_name"] = item.get("company_name") or existing["company_name"]

        ranked = sorted(merged.values(), key=lambda x: (-float(x.get("similarity_score", 0)), x.get("ticker", "")))
        return ranked[:12]

    def _source_summary(self, candidates: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in candidates:
            for src in c.get("sources", [c.get("source", "unknown")]):
                base_src = src.split(":")[0] if ":" in src else src
                counts[base_src] = counts.get(base_src, 0) + 1
        return counts

    def _build_financial_index(self) -> dict[str, dict[str, Path]]:
        """Build a ticker -> {file_key: Path} index from local financial files."""

        if not self.financial_base_dir.exists():
            return {}
        try:
            files = discover_financial_files(str(self.financial_base_dir))
        except Exception:
            return {}
        index: dict[str, dict[str, Path]] = {}
        for path in files:
            stem = path.stem
            parts = stem.split("_", 1)
            ticker_key = parts[0].upper()
            file_key = parts[1] if len(parts) > 1 else "default"
            if ticker_key not in index:
                index[ticker_key] = {}
            index[ticker_key][file_key] = path
        return index

    def _ticker_file_candidates(self, ticker: str) -> list[str]:
        parts = ticker.split(".")
        bare = parts[-1] if len(parts) > 1 else ticker
        candidates = [ticker, bare, ticker.upper(), bare.upper(), ticker.lower(), bare.lower()]
        from app.providers.financial_data_provider import LocalFinancialDataProvider
        for alias in LocalFinancialDataProvider._TICKER_ALIASES.get(ticker.upper(), []):
            if alias not in candidates:
                candidates.append(alias)
        return candidates

    def _extract_latest_metrics(self, files: dict[str, Path]) -> dict[str, Any] | None:
        all_rows: list[dict] = []
        for _key, path in files.items():
            try:
                result = parse_financial_file(path)
                records = result.get("records", []) if isinstance(result, dict) else result
                all_rows.extend(records)
            except Exception:
                continue
        if not all_rows:
            return None

        by_year: dict[int, dict] = {}
        for row in all_rows:
            fy = row.get("fiscal_year", 0)
            if not fy:
                continue
            merged = by_year.setdefault(fy, {"fiscal_year": fy})
            for k, v in row.items():
                if v is not None and k != "raw_source":
                    merged.setdefault(k, v)

        if not by_year:
            return None

        from app.utils.financial_mapping import finalize_financial_row
        for row in by_year.values():
            finalize_financial_row(row)

        years = sorted(by_year.keys())
        latest = by_year[years[-1]]
        revenue = latest.get("revenue")
        if not revenue or float(revenue) <= 0:
            return None

        rev_growth = None
        if len(years) >= 2:
            prev = by_year[years[-2]]
            prev_rev = prev.get("revenue")
            if prev_rev and float(prev_rev) > 0:
                rev_growth = (float(revenue) - float(prev_rev)) / float(prev_rev)

        return {
            "fiscal_year": latest.get("fiscal_year"),
            "revenue": float(revenue),
            "revenue_growth": rev_growth,
            "gross_margin": _to_float(latest.get("gross_margin")),
            "net_margin": _to_float(latest.get("net_margin")),
            "roe": _to_float(latest.get("roe")),
            "roic": _to_float(latest.get("roic")),
            "free_cash_flow": _to_float(latest.get("free_cash_flow")),
        }


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None
