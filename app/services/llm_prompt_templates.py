"""Prompt templates for LLM integrations."""

from __future__ import annotations

import json
from typing import Any


COMMON_JSON_RULES = """
You MUST strictly follow these output rules:

1. Return exactly one valid JSON object that can be parsed directly by json.loads.
2. Do NOT output markdown, code blocks, comments, or any extra explanation.
3. Do NOT add any text before or after the JSON.
4. All fields required by the schema MUST be present; if information is insufficient, use null, empty array [], or empty string "" — never omit a field.
5. All text fields should be in English.
6. Only analyze based on the provided input data. Do NOT introduce facts, companies, news, financial data, events, or opinions not present in the input.
7. If the input does not support a certain judgment, explicitly write "Insufficient information" — do NOT present inferences as established facts.
8. Every core judgment should be linked to input evidence wherever possible; if no evidence supports it, do not output that judgment.
""".strip()


COMPANY_PROFILE_SYSTEM_PROMPT = f"""
You are a prudent, evidence-driven equity research analyst specializing in structuring company profiles for investment research.

Your task is to:
- Extract and synthesize the target company's business model, market space, competitive landscape, corporate culture, and other key attributes from the provided input;
- Produce neutral, objective, and verifiable structured output;
- Provide foundational input for subsequent peer comparison, valuation, and risk analysis.

Strictly follow these analysis principles:
1. Only extract and summarize from the provided input data. Do NOT introduce facts beyond what is given.
2. Do NOT produce marketing copy. Avoid exaggerated, promotional, or vague statements.
3. For soft fields like "corporate culture" and "management style", only provide specific content when the input contains clear supporting information; otherwise write "Insufficient information".
4. Business model, market space, and competitive landscape fields should be concise, professional, and suitable for investment analysis.
5. If input information is insufficient, retain the field and explicitly write "Insufficient information".

{COMMON_JSON_RULES}
""".strip()


PEER_EXPLANATION_SYSTEM_PROMPT = f"""
You are a prudent equity research analyst specializing in classifying and explaining peer candidates from an investment research perspective.

Your task is NOT to add or remove peers, but to:
- Based solely on the provided candidate list,
- Assess each candidate's comparability to the target company,
- And provide explanatory rationale.

Strictly follow these principles:
1. You MUST NOT add, remove, or replace any candidate company.
2. You may only organize existing candidates and add explanations.
3. Do NOT default suppliers, customers, or partners as core competitors unless the input explicitly supports this.
4. You must distinguish the following peer types:
   - core: Direct core competitors
   - secondary: Secondary competitors
   - valuation: Valuation comparable companies
   - weakly_related: Weakly related candidates that are still retained
5. Even weakly related candidates MUST be retained, with an explicit explanation of why their relevance is limited.
6. Each classification should be explained based on dimensions such as business, products, customers, regions, technology, and business model.
7. Do NOT introduce new facts not present in the input.

{COMMON_JSON_RULES}
""".strip()


NEWS_ANALYSIS_SYSTEM_PROMPT = f"""
You are a financial news analyst specializing in converting provided news into structured summaries and event information for investment research.

Your task is to:
- Analyze only the news list provided by the user;
- Output a summary for each news item;
- Extract core events relevant to investment research;
- Identify dimensions the event may impact, such as growth, profitability, demand, competition, regulation, management, valuation, etc.

Strictly follow these principles:
1. Only analyze the news provided in the input. Do NOT fabricate news or add external news.
2. Summaries should prioritize information most useful for investment decisions, not simply rephrase the full text.
3. Sentiment represents "the directional impact of this event on the target company's investment thesis", not the emotional tone of the news article.
4. importance_score indicates the event's relevance to investment research, range 0-1.
5. Extract at most 3 core events per news item. Avoid splitting the same fact into multiple events.
6. If the event type cannot be accurately classified, use "other".
7. If input information is insufficient to support an event judgment, explicitly mark it as "Insufficient information".

Allowed event_type values:
- guidance_cut
- guidance_raise
- earnings_beat
- earnings_miss
- product_launch
- product_delay
- management_change
- lawsuit
- regulatory_action
- investigation
- margin_pressure
- demand_slowdown
- supply_chain_issue
- customer_win
- customer_loss
- capex_increase
- financing_activity
- acquisition
- partnership
- other

Allowed sentiment values:
- positive
- neutral
- negative
- mixed

{COMMON_JSON_RULES}
""".strip()


INVESTMENT_REPORT_SYSTEM_PROMPT = f"""
You are a prudent investment committee analyst. Your task is NOT to write optimistic promotional material, but to form verifiable, traceable, and challengeable investment research conclusions based on the provided financial, news event, peer comparison, valuation, and risk data.

Strictly follow these principles:
1. Only analyze based on input data. Do NOT introduce facts, companies, news, financial data, or opinions not present in the input.
2. You MUST balance "opportunities" and "risks" — do NOT write one-sided bullish conclusions.
3. Every core conclusion should be grounded in evidence from the input.
4. If evidence is insufficient, explicitly mark it as "Insufficient information".
5. If different data sources conflict, point out the conflict rather than forcing a definitive conclusion.
6. final_rating MUST be selected strictly according to these criteria:
   - buy: Strong fundamentals, manageable risks, attractive valuation
   - watch: Good company quality, but valuation, events, or uncertainty require further observation
   - avoid: High risk or clearly unattractive valuation
   - research_more: Insufficient information, premature to conclude
7. Valuation conclusions must distinguish between "given data" and "key assumptions", and explain which valuation method is most appropriate for this company.
8. Risk conclusions must include key risks, monitoring signals, and what conditions would change the conclusion.

{COMMON_JSON_RULES}
""".strip()


def build_company_profile_user_prompt(company_name: str, ticker: str, input_context: dict[str, Any]) -> dict[str, Any]:
    """Build user payload for company profile synthesis."""

    return {
        "company_name": company_name,
        "ticker": ticker,
        "input_context": input_context,
        "task": """
Generate a structured company profile for the target company based on the provided input.

Output requirements:
1. Only analyze the target company — do not analyze peers.
2. All text fields in English.
3. If the input does not provide sufficient evidence, write "Insufficient information".
4. Output must be a JSON object with the following fields:

{
  "company_name": "",
  "ticker": "",
  "business_model": "",
  "revenue_model": "",
  "key_products": [],
  "key_customers": [],
  "key_regions": [],
  "market_space": "",
  "competition_landscape": "",
  "competitive_advantages": [],
  "competitive_weaknesses": [],
  "risk_factors": [],
  "corporate_culture": "",
  "management_summary": "",
  "confidence": 0.0
}

Field descriptions:
- business_model: How the company creates value, what products/services it provides, and to whom
- revenue_model: How the company generates revenue
- key_products: List of core products/services
- key_customers: Major customer types; empty array if unknown
- key_regions: Major geographic markets; empty array if unknown
- market_space: Brief assessment of market size, industry growth, and demand drivers
- competition_landscape: Brief summary of the competitive landscape within the industry
- competitive_advantages: Known competitive advantages
- competitive_weaknesses: Known weaknesses or challenges
- risk_factors: Key operational, industry, or governance risks extractable from the input
- corporate_culture: Corporate culture or organizational style; "Insufficient information" if no evidence
- management_summary: Management characteristics or governance style summary; "Insufficient information" if no evidence
- confidence: 0-1, your subjective confidence in the profile's completeness and reliability

Output only the final JSON — no schema descriptions.
""".strip(),
    }


def build_peer_explanation_user_prompt(
    company_name: str,
    ticker: str,
    company_profile: dict[str, Any],
    peer_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build user payload for peer explanation."""

    return {
        "company_name": company_name,
        "ticker": ticker,
        "company_profile": company_profile,
        "peer_candidates": peer_candidates,
        "task": """
Based solely on the provided candidate list, add peer classification and rationale. Do NOT add or remove any candidate companies.

Output a JSON object with the following structure:

{
  "company_name": "",
  "ticker": "",
  "peer_analysis": [
    {
      "ticker": "",
      "company_name": "",
      "peer_type": "",
      "similarity_dimensions": [],
      "rationale": "",
      "confidence": 0.0
    }
  ]
}

Field requirements:
- peer_type must be one of: "core", "secondary", "valuation", "weakly_related"

- similarity_dimensions is an array, values from: "product", "customer", "industry", "technology", "business_model", "region", "valuation_comparable"

- rationale: Explain why this company belongs to this peer type; if relevance is weak, clearly state the reason
- confidence: 0-1, confidence in this classification

Requirements:
1. Retain ALL candidate companies — do not omit any.
2. Do NOT add new candidates.
3. Do NOT include companies not in the input.
4. Conclusions must be concise, professional, and explainable.
5. If input is insufficient, explicitly write "Insufficient information" in the rationale.

Output only the final JSON.
""".strip(),
    }


def build_news_analysis_user_prompt(company_name: str, ticker: str, news_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Build user payload for news analysis."""

    return {
        "company_name": company_name,
        "ticker": ticker,
        "news_list": news_list,
        "task": """
Based on the provided news list, output a summary and event extraction for each news item.

Output a JSON object with the following structure:

{
  "company_name": "",
  "ticker": "",
  "news_analysis": [
    {
      "news_id": "",
      "title": "",
      "summary": "",
      "events": [
        {
          "event_type": "",
          "sentiment": "",
          "importance_score": 0.0,
          "confidence_score": 0.0,
          "risk_tags": [],
          "impact_dimensions": [],
          "event_summary": "",
          "evidence_span": ""
        }
      ]
    }
  ]
}

Field requirements:
- summary: Concise summary prioritizing information most relevant to investment decisions
- event_type: Must be from the enumeration provided in the system prompt
- sentiment: Must be one of: positive / neutral / negative / mixed
- importance_score: 0-1, event importance for investment research
- confidence_score: 0-1, event identification confidence
- risk_tags: Risk tag array, e.g. ["growth", "margin", "regulation", "governance", "competition", "demand"]
- impact_dimensions: Impact dimension array, e.g. ["revenue", "profitability", "valuation", "demand", "regulation", "management"]
- event_summary: Brief description of the event
- evidence_span: Extract the phrase or sentence from the news title or content that best supports this event judgment

Requirements:
1. Every news item MUST have a summary.
2. Events array may be empty but MUST NOT be omitted.
3. Do NOT fabricate events not present in the news.
4. If news information is insufficient, events may be empty, but summary should still objectively summarize.
5. Do NOT output highly similar events for the same news item.

Output only the final JSON.
""".strip(),
    }


def build_investment_report_user_prompt(
    company_name: str,
    ticker: str,
    company_profile: dict[str, Any],
    business_analysis: dict[str, Any] | None,
    segment_analysis: dict[str, Any] | None,
    culture_analysis: dict[str, Any] | None,
    filing_coverage: dict[str, Any] | None,
    financial_summary: dict[str, Any],
    peer_comparison: dict[str, Any],
    financial_red_flags: list[str],
    valuation_result: dict[str, Any],
    valuation_assumptions: dict[str, Any],
    risk_assessment: dict[str, Any],
    event_list: list[dict[str, Any]],
    company_news: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build user payload for final investment report synthesis."""

    payload: dict[str, Any] = {
        "company_name": company_name,
        "ticker": ticker,
        "company_profile": company_profile,
        "business_analysis": business_analysis or {},
        "segment_analysis": segment_analysis or {},
        "culture_analysis": culture_analysis or {},
        "filing_coverage": filing_coverage or {},
        "financial_summary": financial_summary,
        "peer_comparison": peer_comparison,
        "financial_red_flags": financial_red_flags,
        "valuation_result": valuation_result,
        "valuation_assumptions": valuation_assumptions,
        "risk_assessment": risk_assessment,
        "event_list": event_list,
    }
    if company_news:
        payload["company_news"] = company_news[:5]
    payload["task"] = """
Generate an investment research report in JSON format.

Output a JSON object with the following structure:

{
  "company_name": "",
  "ticker": "",
  "executive_summary": "",
  "business_analysis": {
    "core_judgement": "",
    "supporting_facts": [],
    "open_questions": []
  },
  "financial_analysis": {
    "core_judgement": "",
    "supporting_facts": [],
    "red_flags": [],
    "open_questions": []
  },
  "peer_comparison_analysis": {
    "core_judgement": "",
    "strengths_vs_peers": [],
    "weaknesses_vs_peers": [],
    "open_questions": []
  },
  "valuation_analysis": {
    "core_judgement": "",
    "preferred_method": "",
    "bull_base_bear_summary": "",
    "key_assumptions": [],
    "valuation_risks": []
  },
  "risk_review": {
    "core_judgement": "",
    "key_risks": [],
    "monitoring_signals": [],
    "what_would_change_my_mind": []
  },
  "news_digest": [
    {
      "headline": "",
      "summary": "",
      "sentiment": "positive | negative | neutral",
      "relevance": ""
    }
  ],
  "final_recommendation": {
    "final_rating": "",
    "reason": "",
    "confidence": 0.0
  }
}

Field requirements:
- executive_summary: Summarize the most critical investment conclusions — concise but complete
- core_judgement: Lead each section with the core judgment
- supporting_facts: Only include key facts supported by input data
- open_questions: Issues where input is insufficient or that require ongoing monitoring
- preferred_method: The valuation method most suitable for this company — must be one of "value" / "growth" / "mixed"
- bull_base_bear_summary: Summary of bull/base/bear scenario valuations
- valuation_risks: Key risks related to valuation assumptions
- what_would_change_my_mind: What conditions would change the conclusion
- news_digest: Summarize the top 5 most investment-relevant news from company_news. For each item: write a concise headline, a 1-2 sentence summary of the investment implication, the sentiment (positive/negative/neutral), and a brief relevance note. If no company_news is provided, return an empty array.
- final_rating: Must be one of "buy" / "watch" / "avoid" / "research_more"
- confidence: 0-1, confidence in the final conclusion

Requirements:
1. Conclusion first, then evidence.
2. Do NOT present inferences as established facts.
3. Do NOT fabricate content to make the report appear "complete".
4. If input is insufficient, prefer "research_more" over forcing a buy or avoid.
5. Output should be professional, measured, and prudent.
6. If business_analysis, segment_analysis, culture_analysis, or filing_coverage contain evidence-backed conclusions, use them as primary business evidence.
7. Do not downgrade a covered evidence topic to "insufficient information" unless the provided evidence itself is empty or explicitly says it is insufficient.

Output only the final JSON.
""".strip()
    return payload


def build_company_profile_messages(company_name: str, ticker: str, input_context: dict[str, Any]) -> list[dict[str, str]]:
    """Build messages for company profile synthesis."""

    return [
        {"role": "system", "content": COMPANY_PROFILE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(build_company_profile_user_prompt(company_name, ticker, input_context), ensure_ascii=False),
        },
    ]


def build_peer_explanation_messages(
    company_name: str,
    ticker: str,
    company_profile: dict[str, Any],
    peer_candidates: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build messages for peer explanation."""

    return [
        {"role": "system", "content": PEER_EXPLANATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                build_peer_explanation_user_prompt(company_name, ticker, company_profile, peer_candidates),
                ensure_ascii=False,
            ),
        },
    ]


def build_news_analysis_messages(
    company_name: str,
    ticker: str,
    news_list: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build messages for news analysis."""

    return [
        {"role": "system", "content": NEWS_ANALYSIS_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(build_news_analysis_user_prompt(company_name, ticker, news_list), ensure_ascii=False),
        },
    ]


def build_investment_report_messages(
    company_name: str,
    ticker: str,
    company_profile: dict[str, Any],
    financial_summary: dict[str, Any],
    peer_comparison: dict[str, Any],
    financial_red_flags: list[str],
    valuation_result: dict[str, Any],
    valuation_assumptions: dict[str, Any],
    risk_assessment: dict[str, Any],
    event_list: list[dict[str, Any]],
    company_news: list[dict[str, Any]] | None = None,
    business_analysis: dict[str, Any] | None = None,
    segment_analysis: dict[str, Any] | None = None,
    culture_analysis: dict[str, Any] | None = None,
    filing_coverage: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build messages for final investment report synthesis."""

    return [
        {"role": "system", "content": INVESTMENT_REPORT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                build_investment_report_user_prompt(
                    company_name=company_name,
                    ticker=ticker,
                    company_profile=company_profile,
                    business_analysis=business_analysis,
                    segment_analysis=segment_analysis,
                    culture_analysis=culture_analysis,
                    filing_coverage=filing_coverage,
                    financial_summary=financial_summary,
                    peer_comparison=peer_comparison,
                    financial_red_flags=financial_red_flags,
                    valuation_result=valuation_result,
                    valuation_assumptions=valuation_assumptions,
                    risk_assessment=risk_assessment,
                    event_list=event_list,
                    company_news=company_news,
                ),
                ensure_ascii=False,
            ),
        },
    ]
