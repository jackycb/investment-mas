"""Final report assembly service."""

from __future__ import annotations

import os
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.aliyun_llm_service import AliyunLLMService, get_llm_service

settings = get_settings()
logger = get_logger(__name__)


class ReportService:
    """Compose markdown and JSON research reports."""

    def __init__(self, llm_service: AliyunLLMService | None = None) -> None:
        self.llm_service = llm_service or get_llm_service()

    def build(self, state: dict, model: str | None = None) -> tuple[str, dict[str, Any], str]:
        """Build markdown report, JSON payload, and final rating."""

        risk = state.get("risk_assessment", {})
        recommendation = risk.get("recommendation", "research_more")
        final_rating = recommendation if recommendation in {"buy", "watch", "avoid", "research_more"} else "research_more"
        final_rating_label = self._rating_label(final_rating)
        executive_summary = (
            f"{state['company_name']} ({state['ticker']}) "
            f"current overall conclusion is \"{final_rating_label}\", "
            f"with an overall risk score of {risk.get('overall_risk_score', 'n/a')}. "
            f"This assessment is primarily based on the past 5 years of financial trends, "
            f"relative peer positioning, valuation range, and recent news events."
        )
        business_analysis = state.get("business_analysis", {})
        segment_analysis = state.get("segment_analysis", {})
        culture_analysis = state.get("culture_analysis", {})
        peer_analysis = state.get("peer_analysis", {})
        report_coverage = state.get("report_coverage", {})

        sections = {
            "Business Analysis": {
                "profile": state.get("company_profile", {}),
                "business_analysis": business_analysis,
                "peers": state.get("peer_list", []),
            },
            "Segment Analysis": segment_analysis,
            "Culture & Governance": culture_analysis,
            "Peer Analysis": peer_analysis,
            "Financial Analysis": state.get("financial_summary", {}),
            "Peer Comparison": state.get("peer_comparison", {}),
            "Valuation": {
                "result": state.get("valuation_result", {}),
                "assumptions": state.get("valuation_assumptions", {}),
            },
            "Risk Review": state.get("risk_assessment", {}),
            "Events": state.get("event_list", []),
            "Filing Coverage": state.get("filing_coverage", {}),
            "Report Coverage": report_coverage,
            "Errors": state.get("errors", []),
        }

        ba_judgement = business_analysis.get("core_judgement") or state.get("company_profile", {}).get("business_model", "")
        ba_market = ""
        if isinstance(business_analysis.get("market_space"), dict):
            ba_market = business_analysis["market_space"].get("core_judgement", "")
        ba_market = ba_market or state.get("company_profile", {}).get("market_space", "")
        ba_competition = ""
        if isinstance(business_analysis.get("competition"), dict):
            ba_competition = business_analysis["competition"].get("core_judgement", "")
        ba_competition = ba_competition or state.get("company_profile", {}).get("competition_landscape", "")

        segment_lines: list[str] = []
        if isinstance(segment_analysis, dict) and segment_analysis.get("segments"):
            segment_lines.append(f"- Conglomerate: {'Yes' if segment_analysis.get('is_group_company') else 'No'}")
            for seg in segment_analysis["segments"]:
                seg_name = seg.get("segment_name", "Unknown segment")
                desc = seg.get("business_description", "")
                rev = seg.get("revenue_share")
                rev_str = f" (revenue share {rev:.0%})" if rev else ""
                segment_lines.append(f"- **{seg_name}**{rev_str}: {desc}")

        culture_lines: list[str] = []
        if isinstance(culture_analysis, dict) and culture_analysis.get("core_judgement"):
            culture_lines.append(f"- Core judgement: {culture_analysis['core_judgement']}")
            if culture_analysis.get("management_stability_view"):
                culture_lines.append(f"- Management stability: {culture_analysis['management_stability_view']}")
            if culture_analysis.get("compensation_and_incentive_view"):
                culture_lines.append(f"- Compensation & incentives: {culture_analysis['compensation_and_incentive_view']}")
            for member in culture_analysis.get("management_members", [])[:5]:
                name = member.get("name", "")
                title = member.get("title", "")
                if name:
                    culture_lines.append(f"  - {name} ({title})")

        news_lines: list[str] = []
        for article in state.get("company_news", [])[:5]:
            if isinstance(article, dict) and article.get("title"):
                title = article["title"]
                summary = article.get("summary", "")
                source = article.get("source_name", "")
                pub = article.get("published_at", "")
                date_str = f" ({pub[:10]})" if pub else ""
                source_str = f" — {source}" if source else ""
                line = f"- **{title}**{source_str}{date_str}"
                if summary and summary != title:
                    line += f": {summary}"
                news_lines.append(line)

        coverage_lines: list[str] = []
        if isinstance(report_coverage, dict):
            for w in report_coverage.get("warnings", []):
                coverage_lines.append(f"- ⚠ {w}")

        markdown = "\n".join(
            [
                f"# Investment Research Report: {state['company_name']} ({state['ticker']})",
                "",
                "## 1. Executive Summary",
                executive_summary,
                "",
                "## 2. Business Analysis",
                f"- Business model: {ba_judgement}",
                f"- Market opportunity: {ba_market}",
                f"- Competitive landscape: {ba_competition}",
                "",
                "## 3. Segment Analysis",
                *(segment_lines or ["- No segment breakdown (not a conglomerate or insufficient evidence)."]),
                "",
                "## 4. Culture & Governance",
                *(culture_lines or ["- Insufficient information."]),
                "",
                "## 5. Financial Analysis",
                *[f"- {line}" for line in state.get("financial_summary", {}).get("narrative", [])],
                *[f"- Financial red flag: {flag}" for flag in state.get("financial_red_flags", [])],
                "",
                "## 6. Peer Comparison",
                *[f"- {line}" for line in state.get("peer_comparison", {}).get("summary", [])],
                *self._peer_metrics_table(state.get("peer_analysis", {}), target_ticker=state["ticker"]),
                "",
                "## 7. Valuation Analysis",
                *self._valuation_markdown_lines(state.get("valuation_result", {})),
                "",
                "## 8. Risk Assessment",
                f"- Overall risk score: {risk.get('overall_risk_score', 'n/a')}",
                *[f"- Key risk: {item}" for item in risk.get("key_risks", [])],
                *[f"- Monitoring signal: {item}" for item in risk.get("monitoring_signals", [])],
                "",
                "## 9. Latest News",
                *(news_lines or ["- No recent news available."]),
                "",
                "## 10. Report Quality Check",
                *(coverage_lines or ["- Passed."]),
                *[f"- System note: {item}" for item in state.get("errors", [])],
                "",
                "## 11. Final Recommendation",
                f"Final rating: **{final_rating_label}**",
            ]
        )
        report_json = {
            "ticker": state["ticker"],
            "company_name": state["company_name"],
            "executive_summary": executive_summary,
            "sections": sections,
            "final_rating": final_rating,
            "final_rating_label": final_rating_label,
        }
        if self._should_use_llm_report(state):
            llm_payload = self.llm_service.synthesize_report(self._build_llm_state(state), model=model)
            if isinstance(llm_payload, dict):
                report_json = self._merge_llm_report_payload(report_json, llm_payload)
                llm_rating = report_json.get("final_rating")
                if isinstance(llm_payload.get("executive_summary"), str):
                    report_json["executive_summary"] = llm_payload["executive_summary"]
                if llm_rating in {"buy", "watch", "avoid", "research_more"}:
                    final_rating = str(llm_rating)
                    report_json["final_rating_label"] = self._rating_label(final_rating)
                markdown = self._render_markdown_from_json(report_json, state)
        return markdown, report_json, final_rating

    def _should_use_llm_report(self, state: dict[str, Any]) -> bool:
        """Guard expensive LLM report synthesis in low-signal degraded runs."""

        if not (settings.enable_llm_report and self.llm_service.available):
            return False
        if os.getenv("PYTEST_CURRENT_TEST"):
            return False
        has_financials = bool(state.get("financial_summary", {}).get("years"))
        has_events = bool(state.get("event_list"))
        has_profile_signal = bool(state.get("company_profile", {}).get("business_model"))
        if state.get("errors") and not has_financials and not has_events:
            return False
        return has_financials or has_events or has_profile_signal

    def _peer_metrics_table(self, peer_analysis: dict[str, Any], target_ticker: str = "") -> list[str]:
        """Render a markdown table of peer metric snapshots if available.

        The target company is shown first (if present), followed by peers.
        Revenue is displayed in millions with currency unit.
        """

        snapshots = peer_analysis.get("peer_metric_snapshots", {})
        if not snapshots:
            return []

        def _fmt_row(name: str, m: dict, is_target: bool = False) -> str:
            ccy = m.get("currency", "USD")
            if m.get("revenue"):
                rev_m = m["revenue"] / 1_000_000
                rev = f"{rev_m:,.0f}M {ccy}"
            else:
                rev = "N/A"
            rg = f"{m['revenue_growth']:.1%}" if m.get("revenue_growth") is not None else "N/A"
            gm = f"{m['gross_margin']:.1%}" if m.get("gross_margin") is not None else "N/A"
            nm = f"{m['net_margin']:.1%}" if m.get("net_margin") is not None else "N/A"
            roe = f"{m['roe']:.1%}" if m.get("roe") is not None else "N/A"
            label = f"**{name}**" if is_target else name
            return f"| {label} | {rev} | {rg} | {gm} | {nm} | {roe} |"

        lines = [
            "",
            "### Peer Metric Snapshots",
            "",
            "| Company | Revenue (TTM) | Rev Growth | Gross Margin | Net Margin | ROE |",
            "|---------|--------------|-----------|-------------|-----------|-----|",
        ]

        # Target company first
        if target_ticker in snapshots:
            m = snapshots[target_ticker]
            name = m.get("company_name", target_ticker)
            lines.append(_fmt_row(name, m, is_target=True))

        # Peers (skip target)
        for ticker, m in snapshots.items():
            if ticker == target_ticker:
                continue
            name = m.get("company_name", ticker)
            lines.append(_fmt_row(name, m))

        return lines

    def _valuation_markdown_lines(self, valuation: dict[str, Any]) -> list[str]:
        """Render valuation section lines for the markdown report."""
        ccy = valuation.get("per_share_currency", "")
        blended = valuation.get("blended", {})
        shares = valuation.get("shares_outstanding")
        fx = valuation.get("fx_rate", 1.0)
        legacy = valuation.get("legacy_value_analysis", {})
        epv_available = valuation.get("epv_available", False)

        lines = []
        vi = valuation.get("value_investing", {})
        gr = valuation.get("growth", {})

        if epv_available and legacy.get("status") == "ok":
            lines.append(
                f"- **EPV baseline valuation range: {legacy.get('lower_price_limit', 0):,.2f} - "
                f"{legacy.get('upper_price_limit', 0):,.2f} {legacy.get('quote_currency', ccy)}** "
                f"(midpoint: {legacy.get('midpoint_value', 0):,.2f})"
            )
            lines.append(f"- EPV reporting period: {legacy.get('report_period', 'N/A')}")
            if gr.get("base"):
                lines.append(f"- Growth valuation (reference only, market cap base): {gr['base']:,.0f} (reporting currency)")
        else:
            if vi.get("base"):
                lines.append(f"- Value-multiples market cap (base): {vi['base']:,.0f} (reporting currency)")
            if gr.get("base"):
                lines.append(f"- Growth valuation market cap (base): {gr['base']:,.0f} (reporting currency)")

        if shares:
            lines.append(f"- Shares outstanding: {shares:,.0f}")
        if fx != 1.0:
            lines.append(f"- FX rate (reporting to quote): {fx}")

        if blended.get("base") is not None:
            label = "EPV-weighted" if epv_available else "Blended"
            lines.append(f"- {label} per-share valuation (bear/base/bull): {blended.get('bear', 0):,.2f} / {blended['base']:,.2f} / {blended.get('bull', 0):,.2f} {ccy}")

        current_price = valuation.get("current_price")
        pvv = valuation.get("price_vs_valuation", {})
        if current_price:
            lines.append(f"- **Current stock price: {current_price:,.2f} {ccy}** (source: {valuation.get('price_source', 'unknown')})")
            if pvv.get("signal"):
                signal_label = pvv["signal"].replace("_", " ").title()
                upside = pvv.get("upside_pct")
                upside_str = f" ({upside:+.1f}% vs base)" if upside is not None else ""
                lines.append(f"- **Price signal: {signal_label}**{upside_str}")
            if valuation.get("pe_ratio"):
                lines.append(f"- P/E ratio: {valuation['pe_ratio']:.2f}")
            if valuation.get("pb_ratio"):
                lines.append(f"- P/B ratio: {valuation['pb_ratio']:.2f}")

        if not epv_available and legacy.get("status") == "ok":
            lines.append(
                f"- EPV supplementary range: {legacy.get('lower_price_limit', 0):,.2f} - "
                f"{legacy.get('upper_price_limit', 0):,.2f} {legacy.get('quote_currency', ccy)}"
            )
            if not current_price and legacy.get("close_price"):
                lines.append(f"- Current price: {legacy['close_price']:,.2f} {legacy.get('quote_currency', ccy)}")

        for c in valuation.get("commentary", []):
            lines.append(f"- {c}")
        return lines

    def _slim_valuation_for_llm(self, valuation: dict[str, Any]) -> dict[str, Any]:
        """Extract key per-share valuation data for LLM consumption."""
        legacy = valuation.get("legacy_value_analysis", {})
        epv_available = valuation.get("epv_available", False)
        slim: dict[str, Any] = {
            "per_share_currency": valuation.get("per_share_currency"),
            "current_price": valuation.get("current_price"),
            "price_vs_valuation": valuation.get("price_vs_valuation"),
            "blended_per_share": valuation.get("blended"),
            "shares_outstanding": valuation.get("shares_outstanding"),
            "commentary": valuation.get("commentary", []),
            "epv_available": epv_available,
            "valuation_method": valuation.get("valuation_method", "multiples_blend"),
        }
        if legacy.get("status") == "ok":
            slim["epv_analysis"] = {
                "lower_price_limit": legacy.get("lower_price_limit"),
                "upper_price_limit": legacy.get("upper_price_limit"),
                "midpoint_value": legacy.get("midpoint_value"),
                "close_price": legacy.get("close_price"),
                "report_period": legacy.get("report_period"),
                "quote_currency": legacy.get("quote_currency"),
                "revenue_cagr_3y": legacy.get("revenue_cagr_3y"),
            }
        return slim

    def _build_llm_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Reduce noisy workflow state before sending it to the LLM."""

        business_analysis = state.get("business_analysis", {})
        slim_business: dict[str, Any] = {}
        if isinstance(business_analysis, dict):
            slim_business = {
                "core_judgement": business_analysis.get("core_judgement", ""),
                "business_model_detail": business_analysis.get("business_model_detail", ""),
            }
            if isinstance(business_analysis.get("market_space"), dict):
                slim_business["market_space"] = business_analysis["market_space"].get("core_judgement", "")
            if isinstance(business_analysis.get("competition"), dict):
                slim_business["competition"] = business_analysis["competition"].get("core_judgement", "")

        segment_analysis = state.get("segment_analysis", {})
        slim_segments: dict[str, Any] = {}
        if isinstance(segment_analysis, dict):
            slim_segments = {
                "is_group_company": segment_analysis.get("is_group_company", False),
                "segments": [
                    {
                        "segment_name": s.get("segment_name"),
                        "strategic_role": s.get("strategic_role", ""),
                        "revenue_share": s.get("revenue_share"),
                        "profit_share": s.get("profit_share"),
                        "business_description": s.get("business_description", "")[:200],
                        "unit_economics": s.get("unit_economics", {}),
                    }
                    for s in segment_analysis.get("segments", [])[:6]
                ],
                "segment_level_open_questions": segment_analysis.get("segment_level_open_questions", []),
            }

        culture_analysis = state.get("culture_analysis", {})
        slim_culture: dict[str, Any] = {}
        if isinstance(culture_analysis, dict):
            slim_culture = {
                "core_judgement": culture_analysis.get("core_judgement", ""),
                "management_stability_view": culture_analysis.get("management_stability_view", ""),
                "compensation_and_incentive_view": culture_analysis.get("compensation_and_incentive_view", ""),
                "evidence_supported_observations": culture_analysis.get("evidence_supported_observations", []),
                "missing_evidence": culture_analysis.get("missing_evidence", []),
                "governance_risks": culture_analysis.get("governance_risks", []),
            }

        return {
            "ticker": state.get("ticker"),
            "company_name": state.get("company_name"),
            "company_profile": {
                key: state.get("company_profile", {}).get(key)
                for key in ("business_model", "market_space", "competition_landscape", "corporate_culture", "revenue_model")
            },
            "business_analysis": slim_business,
            "segment_analysis": slim_segments,
            "culture_analysis": slim_culture,
            "filing_coverage": state.get("filing_coverage", {}),
            "peer_list": [
                {"ticker": peer.get("ticker"), "company_name": peer.get("company_name")}
                for peer in state.get("peer_list", [])[:5]
                if isinstance(peer, dict)
            ],
            "financial_summary": {
                key: state.get("financial_summary", {}).get(key)
                for key in ("years", "revenue_cagr", "latest_metrics", "latest_metric_years", "narrative")
            },
            "peer_comparison": {
                "metrics": state.get("peer_comparison", {}).get("metrics", {}),
                "summary": state.get("peer_comparison", {}).get("summary", []),
            },
            "valuation_result": self._slim_valuation_for_llm(state.get("valuation_result", {})),
            "risk_assessment": {
                key: state.get("risk_assessment", {}).get(key)
                for key in ("overall_risk_score", "red_flags", "key_risks", "monitoring_signals", "recommendation")
            },
            "event_list": [
                {
                    "event_type": event.get("event_type"),
                    "sentiment": event.get("sentiment"),
                    "event_summary": event.get("event_summary"),
                }
                for event in state.get("event_list", [])[:10]
                if isinstance(event, dict)
            ],
            "company_news": [
                {
                    "title": article.get("title", ""),
                    "summary": article.get("summary", ""),
                    "source_name": article.get("source_name", ""),
                    "published_at": article.get("published_at", ""),
                }
                for article in state.get("company_news", [])[:5]
                if isinstance(article, dict)
            ],
            "financial_red_flags": state.get("financial_red_flags", []),
            "errors": state.get("errors", []),
            "language": "en",
        }

    def _merge_llm_report_payload(self, report_json: dict[str, Any], llm_payload: dict[str, Any]) -> dict[str, Any]:
        """Map the structured LLM report payload into the app's report JSON format."""

        merged = dict(report_json)
        sections = dict(report_json.get("sections", {}))

        def _section_text(section: dict[str, Any] | None, extras: list[str] | None = None) -> str:
            if not isinstance(section, dict):
                return ""
            lines = [str(section.get("core_judgement") or "Insufficient information").strip()]
            for key in ("supporting_facts", "red_flags", "strengths_vs_peers", "weaknesses_vs_peers", "key_assumptions", "assumption_gaps", "valuation_risks", "key_risks", "monitoring_signals", "what_would_change_my_mind", "open_questions"):
                values = section.get(key)
                if isinstance(values, list) and values:
                    label = {
                        "supporting_facts": "Supporting facts",
                        "red_flags": "Red flags",
                        "strengths_vs_peers": "Strengths vs peers",
                        "weaknesses_vs_peers": "Weaknesses vs peers",
                        "key_assumptions": "Key assumptions",
                        "assumption_gaps": "Assumption gaps (not auditable)",
                        "valuation_risks": "Valuation risks",
                        "key_risks": "Key risks",
                        "monitoring_signals": "Monitoring signals",
                        "what_would_change_my_mind": "What would change my mind",
                        "open_questions": "Open questions",
                    }[key]
                    lines.append(f"{label}:")
                    lines.extend(f"- {item}" for item in values if str(item).strip())
            if extras:
                lines.extend(extras)
            return "\n".join(lines).strip()

        business = llm_payload.get("business_analysis")
        financial = llm_payload.get("financial_analysis")
        peer_comp = llm_payload.get("peer_comparison_analysis")
        valuation = llm_payload.get("valuation_analysis")
        risk_review = llm_payload.get("risk_review")
        final_rec = llm_payload.get("final_recommendation")

        if isinstance(business, dict):
            llm_business_text = _section_text(business)
            if self._is_insufficient_text(llm_business_text):
                fallback_business_text = self._render_existing_business_section(sections.get("Business Analysis"))
                if fallback_business_text:
                    logger.warning("report_llm_business_section_rejected", reason="insufficient_with_existing_business_evidence")
                    sections["Business Analysis"] = fallback_business_text
                else:
                    sections["Business Analysis"] = llm_business_text
            else:
                sections["Business Analysis"] = llm_business_text
        if isinstance(financial, dict):
            sections["Financial Analysis"] = _section_text(financial)
        if isinstance(peer_comp, dict):
            peer_extras: list[str] = []
            excluded = peer_comp.get("excluded_peers", [])
            included = peer_comp.get("included_peer_names", [])
            if included:
                peer_extras.append(f"Peers included in quantitative comparison: {', '.join(included)}")
            if excluded:
                peer_extras.append(f"Peers excluded (weakly related): {', '.join(excluded)}")
            sections["Peer Comparison"] = _section_text(peer_comp, extras=peer_extras)
        if isinstance(valuation, dict):
            extras: list[str] = []
            preferred = valuation.get("preferred_method")
            if preferred:
                extras.append(f"Preferred valuation method: {preferred}")
            summary = valuation.get("bull_base_bear_summary")
            if summary:
                extras.append(f"Bull/base/bear valuation: {summary}")
            sections["Valuation"] = _section_text(valuation, extras=extras)
        if isinstance(risk_review, dict):
            sections["Risk Review"] = _section_text(risk_review)

        # Investment thesis
        thesis = llm_payload.get("investment_thesis")
        if isinstance(thesis, dict) and any(thesis.get(k) for k in ("long_case", "short_case", "variant_perception", "key_debate")):
            sections["Investment Thesis"] = thesis
            merged["investment_thesis"] = thesis

        # Data quality check from LLM
        dq = llm_payload.get("data_quality_check")
        if isinstance(dq, dict):
            merged["llm_data_quality_check"] = dq

        news_digest = llm_payload.get("news_digest")
        if isinstance(news_digest, list) and news_digest:
            sections["Latest News"] = news_digest
        if isinstance(final_rec, dict):
            final_rating = final_rec.get("final_rating")
            reason = str(final_rec.get("reason") or "Insufficient information")
            confidence = final_rec.get("confidence")
            recommendation_text = reason
            if confidence is not None:
                recommendation_text = f"{reason}\nConfidence: {confidence}"
            contradictions = final_rec.get("contradictions_found", [])
            if contradictions:
                recommendation_text += "\nContradictions identified:"
                for c in contradictions:
                    if isinstance(c, dict):
                        recommendation_text += f"\n- [{c.get('area', '')}] {c.get('description', '')}"
            sections["Final Recommendation"] = recommendation_text
            merged["contradictions_found"] = contradictions
            if final_rating in {"buy", "watch", "avoid", "research_more"}:
                merged["final_rating"] = final_rating

        if isinstance(llm_payload.get("executive_summary"), str):
            merged["executive_summary"] = llm_payload["executive_summary"]
        merged["sections"] = sections
        return merged

    def _is_insufficient_text(self, value: Any) -> bool:
        """Return true when a report section is effectively empty despite being present."""

        text = str(value or "").strip().lower()
        if not text:
            return True
        insufficient_markers = (
            "insufficient information",
            "information is insufficient",
            "信息不足",
            "unable to assess",
            "cannot assess",
            "not enough information",
            "无法判断",
        )
        first_line = text.splitlines()[0].strip()
        return any(first_line.startswith(marker) or marker in first_line[:220] for marker in insufficient_markers)

    def _render_existing_business_section(self, section: Any) -> str:
        """Render pre-LLM business section data when the LLM returns an empty judgement."""

        if isinstance(section, str):
            return "" if self._is_insufficient_text(section) else section
        if not isinstance(section, dict):
            return ""

        profile = section.get("profile", {}) if isinstance(section.get("profile"), dict) else {}
        business_analysis = section.get("business_analysis", {}) if isinstance(section.get("business_analysis"), dict) else {}
        peers = section.get("peers", []) if isinstance(section.get("peers"), list) else []

        core = str(business_analysis.get("core_judgement") or profile.get("business_model") or "").strip()
        if self._is_insufficient_text(core):
            return ""
        lines = [core]

        detail = str(business_analysis.get("business_model_detail") or "").strip()
        if detail and detail != core and not self._is_insufficient_text(detail):
            lines.append(f"Business model detail: {detail}")

        market = business_analysis.get("market_space")
        market_text = ""
        if isinstance(market, dict):
            market_text = str(market.get("core_judgement") or "").strip()
        market_text = market_text or str(profile.get("market_space") or "").strip()
        if market_text and not self._is_insufficient_text(market_text):
            lines.append(f"Market opportunity: {market_text}")

        competition = business_analysis.get("competition")
        competition_text = ""
        if isinstance(competition, dict):
            competition_text = str(competition.get("core_judgement") or "").strip()
        competition_text = competition_text or str(profile.get("competition_landscape") or "").strip()
        if competition_text and not self._is_insufficient_text(competition_text):
            lines.append(f"Competitive landscape: {competition_text}")

        if peers:
            peer_names = [
                str(peer.get("company_name") or peer.get("ticker") or "").strip()
                for peer in peers[:5]
                if isinstance(peer, dict) and (peer.get("company_name") or peer.get("ticker"))
            ]
            if peer_names:
                lines.append(f"Peer candidates: {', '.join(peer_names)}")

        evidence_refs = business_analysis.get("supporting_evidence_refs")
        if isinstance(evidence_refs, list) and evidence_refs:
            lines.append("Supporting evidence refs:")
            for ref in evidence_refs[:5]:
                if isinstance(ref, dict):
                    filing_id = ref.get("filing_id")
                    section_name = ref.get("section")
                    lines.append(f"- filing_id={filing_id}, section={section_name}")
        return "\n".join(lines).strip()

    def _render_markdown_from_json(self, report_json: dict[str, Any], state: dict) -> str:
        """Render markdown from structured JSON report output."""

        sections = report_json.get("sections", {})
        errors = state.get("errors", [])
        report_coverage = state.get("report_coverage", {})
        coverage_warnings = report_coverage.get("warnings", []) if isinstance(report_coverage, dict) else []

        news_lines = self._render_news_digest_lines(sections.get("Latest News"), state)
        thesis_lines = self._render_investment_thesis(sections.get("Investment Thesis"))
        data_quality_lines = self._render_data_quality(report_json)

        return "\n".join(
            [
                f"# Investment Research Report: {report_json.get('company_name', state.get('company_name', ''))} ({report_json.get('ticker', state.get('ticker', ''))})",
                "",
                "## 1. Executive Summary",
                str(report_json.get("executive_summary", "")),
                "",
                "## 2. Business Analysis",
                str(sections.get("Business Analysis", "")),
                "",
                "## 3. Segment Analysis",
                self._render_segment_section(sections.get("Segment Analysis")),
                "",
                "## 4. Culture & Governance",
                self._render_culture_section(sections.get("Culture & Governance")),
                "",
                "## 5. Financial Analysis",
                str(sections.get("Financial Analysis", "")),
                "",
                "## 6. Peer Comparison",
                str(sections.get("Peer Comparison", "")),
                *self._peer_metrics_table(sections.get("Peer Analysis", {}) if isinstance(sections.get("Peer Analysis"), dict) else {}, target_ticker=state.get("ticker", "")),
                "",
                "## 7. Valuation Analysis",
                str(sections.get("Valuation", "")),
                "",
                "## 8. Risk Assessment",
                str(sections.get("Risk Review", "")),
                "",
                "## 9. Investment Thesis",
                *thesis_lines,
                "",
                "## 10. Latest News",
                *news_lines,
                "",
                "## 11. Data Quality & Audit",
                *data_quality_lines,
                *[f"- {w}" for w in coverage_warnings],
                *[f"- System note: {item}" for item in errors],
                "",
                "## 12. Final Recommendation",
                str(
                    sections.get(
                        "Final Recommendation",
                        f"Final rating: **{report_json.get('final_rating_label', self._rating_label(report_json.get('final_rating', 'research_more')))}**",
                    )
                ),
            ]
        )

    def _render_investment_thesis(self, thesis: Any) -> list[str]:
        """Render investment thesis section."""
        if not isinstance(thesis, dict):
            return ["- No investment thesis generated."]
        lines: list[str] = []
        if thesis.get("long_case"):
            lines.append(f"**Long case:** {thesis['long_case']}")
        if thesis.get("short_case"):
            lines.append(f"**Short case:** {thesis['short_case']}")
        if thesis.get("variant_perception"):
            lines.append(f"**Variant perception:** {thesis['variant_perception']}")
        if thesis.get("key_debate"):
            lines.append(f"**Key debate:** {thesis['key_debate']}")
        return lines or ["- No investment thesis generated."]

    def _render_data_quality(self, report_json: dict[str, Any]) -> list[str]:
        """Render data quality and valuation audit section."""
        lines: list[str] = []
        va = report_json.get("valuation_audit", {})
        if isinstance(va, dict) and va.get("issues"):
            lines.append("### Valuation Audit")
            passed = va.get("passed", False)
            lines.append(f"- Audit passed: **{'Yes' if passed else 'No'}**")
            for issue in va.get("issues", []):
                lines.append(f"- {issue}")

        rd = report_json.get("rating_decision", {})
        if isinstance(rd, dict) and rd.get("rating_constraints_triggered"):
            lines.append("### Rating Constraints")
            for rule in rd["rating_constraints_triggered"]:
                lines.append(f"- {rule}")

        llm_dq = report_json.get("llm_data_quality_check", {})
        if isinstance(llm_dq, dict):
            missing = llm_dq.get("missing_critical_data", [])
            if missing:
                lines.append("### Missing Critical Data")
                for item in missing:
                    lines.append(f"- {item}")
            suspected = llm_dq.get("suspected_data_issues", [])
            if suspected:
                lines.append("### Suspected Data Issues")
                for item in suspected:
                    lines.append(f"- {item}")

        return lines or ["- No data quality issues detected."]

    def _render_news_digest_lines(self, news_section: Any, state: dict) -> list[str]:
        """Render news digest from LLM output or raw state."""
        if isinstance(news_section, list) and news_section:
            lines: list[str] = []
            for item in news_section[:5]:
                if isinstance(item, dict):
                    headline = item.get("headline", "")
                    summary = item.get("summary", "")
                    sentiment = item.get("sentiment", "")
                    relevance = item.get("relevance", "")
                    sentiment_tag = f" [{sentiment}]" if sentiment else ""
                    line = f"- **{headline}**{sentiment_tag}: {summary}"
                    if relevance:
                        line += f" _{relevance}_"
                    lines.append(line)
            return lines or ["- No recent news available."]
        news_lines: list[str] = []
        for article in state.get("company_news", [])[:5]:
            if isinstance(article, dict) and article.get("title"):
                title = article["title"]
                summary = article.get("summary", "")
                source = article.get("source_name", "")
                pub = article.get("published_at", "")
                date_str = f" ({pub[:10]})" if pub else ""
                source_str = f" — {source}" if source else ""
                line = f"- **{title}**{source_str}{date_str}"
                if summary and summary != title:
                    line += f": {summary}"
                news_lines.append(line)
        return news_lines or ["- No recent news available."]

    def _render_segment_section(self, segment_data: Any) -> str:
        """Render segment analysis as readable markdown."""
        if isinstance(segment_data, str):
            return segment_data
        if not isinstance(segment_data, dict):
            return "No segment breakdown."
        lines: list[str] = []
        lines.append(f"- Conglomerate: {'Yes' if segment_data.get('is_group_company') else 'No'}")
        for seg in segment_data.get("segments", []):
            if not isinstance(seg, dict):
                continue
            name = seg.get("segment_name", "Unknown")
            desc = seg.get("business_description", "")
            rev = seg.get("revenue_share")
            rev_str = f" (revenue share {rev:.0%})" if rev else ""
            lines.append(f"- **{name}**{rev_str}: {desc}")
        return "\n".join(lines) if lines else "No segment breakdown."

    def _render_culture_section(self, culture_data: Any) -> str:
        """Render culture & governance as readable markdown."""
        if isinstance(culture_data, str):
            return culture_data
        if not isinstance(culture_data, dict):
            return "Insufficient information."
        lines: list[str] = []
        if culture_data.get("core_judgement"):
            lines.append(f"- Core judgement: {culture_data['core_judgement']}")
        if culture_data.get("management_stability_view"):
            lines.append(f"- Management stability: {culture_data['management_stability_view']}")
        if culture_data.get("compensation_and_incentive_view"):
            lines.append(f"- Compensation & incentives: {culture_data['compensation_and_incentive_view']}")
        for member in culture_data.get("management_members", [])[:5]:
            if isinstance(member, dict) and member.get("name"):
                lines.append(f"  - {member['name']} ({member.get('title', '')})")
        evidence_obs = culture_data.get("evidence_supported_observations", [])
        if evidence_obs:
            lines.append("- Evidence-supported observations:")
            for obs in evidence_obs[:5]:
                lines.append(f"  - {obs}")
        inferred_obs = culture_data.get("inferred_observations", [])
        if inferred_obs:
            lines.append("- Inferred observations (not directly stated):")
            for obs in inferred_obs[:3]:
                lines.append(f"  - {obs}")
        missing = culture_data.get("missing_evidence", [])
        if missing:
            lines.append("- Missing governance data:")
            for item in missing[:5]:
                lines.append(f"  - {item}")
        gov_risks = culture_data.get("governance_risks", [])
        if gov_risks:
            lines.append("- Governance risks:")
            for risk in gov_risks[:3]:
                lines.append(f"  - {risk}")
        return "\n".join(lines) if lines else "Insufficient information."

    def _rating_label(self, rating: str) -> str:
        """Map internal rating code to English label."""

        return {
            "buy": "Buy",
            "watch": "Watch",
            "avoid": "Avoid",
            "research_more": "Research More",
        }.get(rating, "Research More")
