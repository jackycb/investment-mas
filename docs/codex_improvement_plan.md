# Investment MAS Codex 改进优化方案

> 本文档基于 `docs/investment_mas_design_spec.md` 和 `docs/investment_mas_checklist.md` 的系统 review 生成，可直接作为 Codex 的任务指令执行。

---

## 背景

项目整体架构已就位（7 层分层、Provider 抽象、LangGraph 工作流、10 张数据库表、3 个 API 端点），但 review 发现以下关键缺陷需要修复：

1. 5/6 个 LangGraph 节点缺少 try/except 容错，任一节点异常将导致整条工作流崩溃
2. ResearchState 缺少 `total=False`，与节点渐进填充状态的设计矛盾
3. Alembic 迁移缺少 11 个数据库索引
4. 5 个核心 Service 缺少独立测试
5. config.py 缺少 NEWS_PROVIDER 和 COMPANY_INFO_PROVIDER 配置项
6. ValuationAgentNode 未使用 company_profile 输入
7. InvestmentExpertNode 丢弃了 report_json

---

## 任务 1：ResearchState 添加 `total=False` 并新增 `report_json` 字段

**文件**: `app/graph/state.py`

**当前代码（第 6 行）**:
```python
class ResearchState(TypedDict):
```

**改为**:
```python
class ResearchState(TypedDict, total=False):
```

**原因**: 设计文档 §6.3 明确要求 `total=False`。当前所有字段为 required，与节点渐进填充的设计矛盾。LangGraph 节点只返回自己负责的字段子集，`total=False` 允许这种部分更新。

**同时新增字段**（在 `final_rating` 之后、`errors` 之前）:
```python
    report_json: dict
```

**同步更新 `create_initial_state()` 函数**，在返回值中添加:
```python
    report_json={},
```

---

## 任务 2：为 5 个缺少容错的 LangGraph 节点添加 try/except

**参考模式**: `app/graph/nodes/financial_analyst.py` 是当前唯一正确处理错误的节点。所有其他节点应参照此模式。

**核心原则**（来自设计文档 §6.4 和 §13）:
- 节点失败时将错误信息 append 到 `state["errors"]`
- 返回默认空值，让后续节点继续执行
- 不因单个模块失败导致整条链中断

### 2.1 修改 `app/graph/nodes/company_profiler.py`

当前代码:
```python
def __call__(self, state: ResearchState) -> dict:
    profile, peers, _company = self.service.build_profile(self.session, state["ticker"], state["company_name"])
    return {"company_profile": profile, "peer_list": peers}
```

改为:
```python
def __call__(self, state: ResearchState) -> dict:
    try:
        profile, peers, _company = self.service.build_profile(
            self.session, state["ticker"], state["company_name"],
        )
    except Exception as exc:
        return {
            "company_profile": {},
            "peer_list": [],
            "errors": state.get("errors", []) + [f"CompanyProfiler failed: {exc}"],
        }
    return {"company_profile": profile, "peer_list": peers}
```

### 2.2 修改 `app/graph/nodes/news_collector.py`

当前代码:
```python
def __call__(self, state: ResearchState) -> dict:
    company_news, peer_news = self.news_service.collect_news(
        self.session, state["ticker"], state["company_name"], state["peer_list"]
    )
    event_list = self.event_service.extract_events(company_news + peer_news)
    return {"company_news": company_news, "peer_news": peer_news, "event_list": event_list}
```

改为:
```python
def __call__(self, state: ResearchState) -> dict:
    errors: list[str] = []
    try:
        company_news, peer_news = self.news_service.collect_news(
            self.session, state["ticker"], state["company_name"], state.get("peer_list", []),
        )
    except Exception as exc:
        company_news, peer_news = [], []
        errors.append(f"NewsCollector failed to collect news: {exc}")
    try:
        event_list = self.event_service.extract_events(company_news + peer_news)
    except Exception as exc:
        event_list = []
        errors.append(f"NewsCollector failed to extract events: {exc}")
    result: dict = {"company_news": company_news, "peer_news": peer_news, "event_list": event_list}
    if errors:
        result["errors"] = state.get("errors", []) + errors
    return result
```

### 2.3 修改 `app/graph/nodes/valuation_agent.py`

当前代码:
```python
def __call__(self, state: ResearchState) -> dict:
    result, assumptions = self.service.run(state["financial_summary"], state["peer_comparison"])
    return {"valuation_result": result, "valuation_assumptions": assumptions}
```

改为（同时接入 company_profile，见任务 4）:
```python
def __call__(self, state: ResearchState) -> dict:
    try:
        result, assumptions = self.service.run(
            state.get("financial_summary", {}),
            state.get("peer_comparison", {}),
            state.get("company_profile", {}),
        )
    except Exception as exc:
        return {
            "valuation_result": {},
            "valuation_assumptions": {},
            "errors": state.get("errors", []) + [f"ValuationAgent failed: {exc}"],
        }
    return {"valuation_result": result, "valuation_assumptions": assumptions}
```

### 2.4 修改 `app/graph/nodes/risk_analyst.py`

当前代码:
```python
def __call__(self, state: ResearchState) -> dict:
    assessment = self.service.assess(
        state["financial_summary"],
        state["peer_comparison"],
        state["financial_red_flags"],
        state["event_list"],
        state["valuation_result"],
    )
    return {"risk_assessment": assessment}
```

改为:
```python
def __call__(self, state: ResearchState) -> dict:
    try:
        assessment = self.service.assess(
            state.get("financial_summary", {}),
            state.get("peer_comparison", {}),
            state.get("financial_red_flags", []),
            state.get("event_list", []),
            state.get("valuation_result", {}),
        )
    except Exception as exc:
        return {
            "risk_assessment": {},
            "errors": state.get("errors", []) + [f"RiskAnalyst failed: {exc}"],
        }
    return {"risk_assessment": assessment}
```

### 2.5 修改 `app/graph/nodes/investment_expert.py`

当前代码:
```python
def __call__(self, state: ResearchState) -> dict:
    report_markdown, _report_json, final_rating = self.service.build(state)
    return {"final_report": report_markdown, "final_rating": final_rating}
```

改为（同时传递 report_json，见任务 3）:
```python
def __call__(self, state: ResearchState) -> dict:
    try:
        report_markdown, report_json, final_rating = self.service.build(state)
    except Exception as exc:
        return {
            "final_report": "",
            "final_rating": "research_more",
            "report_json": {},
            "errors": state.get("errors", []) + [f"InvestmentExpert failed: {exc}"],
        }
    return {"final_report": report_markdown, "final_rating": final_rating, "report_json": report_json}
```

### 2.6 同步修改 `app/graph/nodes/financial_analyst.py`

当前代码中使用 `state["errors"]` 直接访问，改为 `state.get("errors", [])` 以配合 `total=False`:

将第 27 行:
```python
"errors": state["errors"] + warnings,
```
改为:
```python
"errors": state.get("errors", []) + warnings,
```

同时将整个 `__call__` 也包裹 try/except 以防 service 异常:
```python
def __call__(self, state: ResearchState) -> dict:
    try:
        financial_summary, reports, warnings = self.service.summarize_company(
            state["ticker"], state["company_name"],
        )
        peer_comparison = self.service.compare_peers(
            state["ticker"], state["company_name"], reports, state.get("peer_list", []),
        )
        red_flags = self.service.identify_red_flags(financial_summary, peer_comparison)
    except Exception as exc:
        return {
            "financial_summary": {},
            "peer_comparison": {},
            "financial_red_flags": [],
            "errors": state.get("errors", []) + [f"FinancialAnalyst failed: {exc}"],
        }
    return {
        "financial_summary": financial_summary,
        "peer_comparison": peer_comparison,
        "financial_red_flags": red_flags,
        "errors": state.get("errors", []) + warnings,
    }
```

---

## 任务 3：InvestmentExpertNode 传递 report_json

已在任务 1 中为 ResearchState 添加了 `report_json: dict` 字段，任务 2.5 中已修改节点返回 `report_json`。

**同步检查** `app/api/routes/research.py`：确保 API 路由从 `final_state` 中读取 `report_json`（而非重新构建），如果当前 API 层已经调用 ReportService.build() 获取 report_json，则保持不变即可。

---

## 任务 4：ValuationAgentNode 接入 company_profile

**设计文档 §6.2.4 要求**: ValuationAgent 的输入包含 `company_profile`、`financial_summary`、`peer_comparison`。

节点改动已在任务 2.3 中完成。

**还需修改 `app/services/valuation_service.py` 的 `run()` 方法签名**:

当前签名:
```python
def run(self, financial_summary: dict, peer_comparison: dict) -> tuple[dict, dict]:
```

改为:
```python
def run(self, financial_summary: dict, peer_comparison: dict, company_profile: dict | None = None) -> tuple[dict, dict]:
```

在方法体中，可以利用 `company_profile` 来调整估值参数（例如根据行业属性选择不同的 exit multiple），第一版至少预留参数接口即可，不强制使用。

---

## 任务 5：config.py 补充 NEWS_PROVIDER 和 COMPANY_INFO_PROVIDER

**文件**: `app/core/config.py`

在 Settings 类中（`local_financial_base_dir` 之后）添加:
```python
    news_provider: str = "mock"
    company_info_provider: str = "mock"
```

**同步修改**: 在相关 Node 或 Service 构造时读取 settings 判断实例化哪个 provider。当前 MVP 阶段只有 mock 实现，所以只需预留配置项，默认值为 "mock"。

---

## 任务 6：新建 Alembic 迁移补充缺失索引

**文件**: 新建 `alembic/versions/20260419_0002_add_missing_indexes.py`

```python
"""add missing indexes for foreign keys and query fields"""

from alembic import op

revision = "20260419_0002"
down_revision = "20260419_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_company_profiles_company_id", "company_profiles", ["company_id"])
    op.create_index("ix_peer_mappings_company_id", "peer_mappings", ["company_id"])
    op.create_index("ix_peer_mappings_peer_company_id", "peer_mappings", ["peer_company_id"])
    op.create_index("ix_financial_reports_company_id", "financial_reports", ["company_id"])
    op.create_index("ix_financial_reports_fiscal_year", "financial_reports", ["fiscal_year"])
    op.create_index("ix_financial_peer_snapshots_company_id", "financial_peer_snapshots", ["company_id"])
    op.create_index("ix_news_articles_company_id", "news_articles", ["company_id"])
    op.create_index("ix_news_events_article_id", "news_events", ["article_id"])
    op.create_index("ix_news_events_company_id", "news_events", ["company_id"])
    op.create_index("ix_risk_assessments_company_id", "risk_assessments", ["company_id"])
    op.create_index("ix_valuation_results_company_id", "valuation_results", ["company_id"])
    op.create_index("ix_research_reports_company_id", "research_reports", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_research_reports_company_id", table_name="research_reports")
    op.drop_index("ix_valuation_results_company_id", table_name="valuation_results")
    op.drop_index("ix_risk_assessments_company_id", table_name="risk_assessments")
    op.drop_index("ix_news_events_company_id", table_name="news_events")
    op.drop_index("ix_news_events_article_id", table_name="news_events")
    op.drop_index("ix_news_articles_company_id", table_name="news_articles")
    op.drop_index("ix_financial_peer_snapshots_company_id", table_name="financial_peer_snapshots")
    op.drop_index("ix_financial_reports_fiscal_year", table_name="financial_reports")
    op.drop_index("ix_financial_reports_company_id", table_name="financial_reports")
    op.drop_index("ix_peer_mappings_peer_company_id", table_name="peer_mappings")
    op.drop_index("ix_peer_mappings_company_id", table_name="peer_mappings")
    op.drop_index("ix_company_profiles_company_id", table_name="company_profiles")
```

---

## 任务 7：补齐 5 个缺失测试文件

### 7.1 新建 `tests/test_financial_service.py`

测试 `FinancialService` 的以下场景:

```python
"""Tests for FinancialService."""

import pytest
from app.services.financial_service import FinancialService


class FakeProvider:
    """Minimal in-memory provider for testing."""

    def __init__(self, data: dict[str, list[dict]]):
        self._data = data

    def load_company_financials(self, ticker: str, company_name: str, years: int = 5) -> list[dict]:
        return self._data.get(ticker, [])

    def load_peer_financials(self, peer_tickers: list[str], years: int = 5) -> dict[str, list[dict]]:
        return {t: self._data.get(t, []) for t in peer_tickers}


def _make_records(years: list[int], revenue_start: float = 100_000) -> list[dict]:
    """Generate simplified financial records for testing."""
    records = []
    for i, y in enumerate(years):
        rev = revenue_start * (1.1 ** i)
        records.append({
            "fiscal_year": y,
            "revenue": rev,
            "gross_profit": rev * 0.6,
            "net_income": rev * 0.2,
            "operating_cash_flow": rev * 0.22,
            "free_cash_flow": rev * 0.18,
            "total_equity": rev * 2,
            "gross_margin": 0.6,
            "net_margin": 0.2,
            "roe": 0.1,
        })
    return records


class TestSummarizeCompany:
    def test_happy_path_returns_summary_with_cagr(self):
        provider = FakeProvider({"NVDA": _make_records([2020, 2021, 2022, 2023, 2024])})
        svc = FinancialService(provider=provider)
        summary, reports, warnings = svc.summarize_company("NVDA", "NVIDIA")
        assert "revenue_cagr" in summary
        assert summary["revenue_cagr"] > 0
        assert len(reports) == 5
        assert len(warnings) == 0

    def test_empty_data_returns_empty_summary(self):
        provider = FakeProvider({})
        svc = FinancialService(provider=provider)
        summary, reports, warnings = svc.summarize_company("UNKNOWN", "Unknown Corp")
        assert reports == []

    def test_single_year_no_cagr_crash(self):
        provider = FakeProvider({"X": _make_records([2024])})
        svc = FinancialService(provider=provider)
        summary, reports, warnings = svc.summarize_company("X", "X Corp")
        assert len(reports) == 1


class TestIdentifyRedFlags:
    def test_declining_revenue_flagged(self):
        """Revenue growth declining for 2+ consecutive years should be a red flag."""
        svc = FinancialService(provider=FakeProvider({}))
        summary = {
            "revenue_series": [100, 95, 85],
            "gross_margin_trend": "declining",
            "ocf_vs_net_income": {"ratio": 0.5},
        }
        flags = svc.identify_red_flags(summary, {})
        assert len(flags) > 0

    def test_healthy_company_no_flags(self):
        svc = FinancialService(provider=FakeProvider({}))
        summary = {
            "revenue_series": [100, 120, 150],
            "gross_margin_trend": "improving",
            "ocf_vs_net_income": {"ratio": 1.1},
        }
        flags = svc.identify_red_flags(summary, {})
        assert len(flags) == 0


class TestComparePeers:
    def test_comparison_includes_avg_and_median(self):
        data = {
            "NVDA": _make_records([2024]),
            "AMD": _make_records([2024], revenue_start=80_000),
            "AVGO": _make_records([2024], revenue_start=120_000),
        }
        provider = FakeProvider(data)
        svc = FinancialService(provider=provider)
        _, reports, _ = svc.summarize_company("NVDA", "NVIDIA")
        peers = [{"ticker": "AMD"}, {"ticker": "AVGO"}]
        comparison = svc.compare_peers("NVDA", "NVIDIA", reports, peers)
        assert comparison  # non-empty

    def test_empty_peers_no_crash(self):
        provider = FakeProvider({"NVDA": _make_records([2024])})
        svc = FinancialService(provider=provider)
        _, reports, _ = svc.summarize_company("NVDA", "NVIDIA")
        comparison = svc.compare_peers("NVDA", "NVIDIA", reports, [])
        assert isinstance(comparison, dict)
```

### 7.2 新建 `tests/test_event_extraction_service.py`

```python
"""Tests for EventExtractionService."""

from app.services.event_extraction_service import EventExtractionService


def _article(title: str, summary: str = "") -> dict:
    return {"title": title, "summary": summary or title, "ticker": "NVDA", "published_at": "2024-06-01"}


class TestExtractEvents:
    def test_guidance_cut_detected(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("Company cuts guidance amid slowing demand")])
        types = [e["event_type"] for e in events]
        assert "guidance_cut" in types

    def test_lawsuit_detected(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("Firm faces major lawsuit over patent infringement")])
        types = [e["event_type"] for e in events]
        assert "lawsuit" in types

    def test_regulatory_action_detected(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("SEC regulatory action against company")])
        types = [e["event_type"] for e in events]
        assert "regulatory_action" in types

    def test_management_change_detected(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("CEO resigns, new management appointed")])
        types = [e["event_type"] for e in events]
        assert "management_change" in types

    def test_margin_pressure_detected(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("Margin pressure intensifies due to rising costs")])
        types = [e["event_type"] for e in events]
        assert "margin_pressure" in types

    def test_demand_slowdown_detected(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("Demand slowdown hits semiconductor sector")])
        types = [e["event_type"] for e in events]
        assert "demand_slowdown" in types

    def test_unmatched_article_returns_general_update(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("Company hosts annual developer conference")])
        types = [e["event_type"] for e in events]
        assert "general_update" in types

    def test_empty_input_returns_empty_list(self):
        svc = EventExtractionService()
        assert svc.extract_events([]) == []

    def test_event_structure_complete(self):
        svc = EventExtractionService()
        events = svc.extract_events([_article("Guidance cut announced")])
        event = events[0]
        required_keys = {"event_type", "sentiment", "importance_score", "confidence_score", "risk_tags", "entities", "event_summary", "impact_dimensions"}
        assert required_keys.issubset(event.keys())
```

### 7.3 新建 `tests/test_report_service.py`

```python
"""Tests for ReportService."""

from app.services.report_service import ReportService


def _full_state() -> dict:
    return {
        "ticker": "NVDA",
        "company_name": "NVIDIA",
        "company_profile": {"business_model": "GPU computing", "market_space": "AI/HPC", "competition_landscape": "AMD, Intel"},
        "peer_list": [{"ticker": "AMD", "company_name": "AMD"}],
        "company_news": [],
        "peer_news": [],
        "event_list": [],
        "financial_summary": {"revenue_cagr": 0.25, "narrative": ["Strong revenue growth"]},
        "peer_comparison": {"summary": ["Outperforms peers on margin"]},
        "financial_red_flags": [],
        "valuation_result": {"value_investing": {"bull": 150, "base": 120, "bear": 90}, "growth": {"bull": 180, "base": 140, "bear": 100}, "blended": {"bull": 165, "base": 130, "bear": 95}},
        "valuation_assumptions": {"discount_rate": 0.1},
        "risk_assessment": {"overall_risk_score": 35, "recommendation": "watch", "key_risks": ["High valuation"], "monitoring_signals": ["Track margin trend"], "red_flags": []},
        "errors": [],
    }


class TestReportBuild:
    def test_returns_markdown_json_and_rating(self):
        svc = ReportService()
        md, json_report, rating = svc.build(_full_state())
        assert isinstance(md, str)
        assert isinstance(json_report, dict)
        assert rating in {"buy", "watch", "avoid", "research_more"}

    def test_markdown_contains_all_7_sections(self):
        svc = ReportService()
        md, _, _ = svc.build(_full_state())
        for section in ["Executive Summary", "Business Analysis", "Financial Analysis", "Peer Comparison", "Valuation", "Risk Review", "Final Recommendation"]:
            assert section in md, f"Missing section: {section}"

    def test_json_has_required_keys(self):
        svc = ReportService()
        _, json_report, _ = svc.build(_full_state())
        assert "ticker" in json_report
        assert "executive_summary" in json_report
        assert "sections" in json_report
        assert "final_rating" in json_report

    def test_partial_state_does_not_crash(self):
        svc = ReportService()
        minimal = {"ticker": "X", "company_name": "X Corp"}
        md, json_report, rating = svc.build(minimal)
        assert rating == "research_more"
        assert "X Corp" in md

    def test_errors_included_in_report(self):
        svc = ReportService()
        state = _full_state()
        state["errors"] = ["FinancialAnalyst failed: timeout"]
        md, json_report, _ = svc.build(state)
        assert "timeout" in md
        assert "FinancialAnalyst failed" in str(json_report)
```

### 7.4 新建 `tests/test_news_service.py`

```python
"""Tests for NewsService."""

from app.services.news_service import NewsService
from app.providers.news_provider import MockNewsProvider


class TestNewsService:
    def test_collect_news_returns_company_and_peer_news(self):
        svc = NewsService(provider=MockNewsProvider())
        company_news, peer_news = svc.collect_news(
            session=None, ticker="NVDA", company_name="NVIDIA",
            peers=[{"ticker": "AMD", "company_name": "AMD"}],
        )
        assert isinstance(company_news, list)
        assert isinstance(peer_news, list)
        assert len(company_news) > 0

    def test_empty_peers_returns_empty_peer_news(self):
        svc = NewsService(provider=MockNewsProvider())
        company_news, peer_news = svc.collect_news(
            session=None, ticker="NVDA", company_name="NVIDIA", peers=[],
        )
        assert isinstance(peer_news, list)
```

**注意**: 如果 `NewsService.collect_news()` 的 session 参数为 None 时会崩溃（因为 DB fallback 逻辑），需要在 service 中添加 `if session is not None` 的安全检查，或者在测试中使用 conftest.py 中的 SQLite session。根据实际代码决定。

### 7.5 新建 `tests/test_company_profile_service.py`

```python
"""Tests for CompanyProfileService."""

from app.services.company_profile_service import CompanyProfileService
from app.providers.company_info_provider import MockCompanyInfoProvider


class TestCompanyProfileService:
    def test_build_profile_returns_profile_and_peers(self, session):
        svc = CompanyProfileService(provider=MockCompanyInfoProvider())
        profile, peers, company = svc.build_profile(session, "NVDA", "NVIDIA")
        assert isinstance(profile, dict)
        assert isinstance(peers, list)
        assert "business_model" in profile

    def test_unknown_ticker_uses_mock_fallback(self, session):
        svc = CompanyProfileService(provider=MockCompanyInfoProvider())
        profile, peers, company = svc.build_profile(session, "UNKNOWN", "Unknown Corp")
        assert isinstance(profile, dict)
        assert isinstance(peers, list)
```

**注意**: 此文件使用 conftest.py 中的 `session` fixture。

---

## 任务 8：扩展现有测试的边界场景

### 8.1 扩展 `tests/test_valuation_service.py`

添加以下测试:

```python
def test_zero_revenue_does_not_crash(self):
    """ValuationService should handle zero revenue gracefully."""
    svc = ValuationService()
    summary = {"revenue_cagr": 0.0, "latest_revenue": 0, "latest_fcf": 0, "latest_net_income": 0}
    result, assumptions = svc.run(summary, {})
    assert isinstance(result, dict)

def test_missing_fields_returns_partial_result(self):
    """Missing financial fields should not crash, should return partial valuation."""
    svc = ValuationService()
    summary = {}  # completely empty
    result, assumptions = svc.run(summary, {})
    assert isinstance(result, dict)

def test_negative_fcf_handled(self):
    """Negative free cash flow should still produce a result (possibly low/zero valuations)."""
    svc = ValuationService()
    summary = {"revenue_cagr": -0.1, "latest_revenue": 100_000, "latest_fcf": -50_000, "latest_net_income": -30_000}
    result, assumptions = svc.run(summary, {})
    assert isinstance(result, dict)
```

### 8.2 扩展 `tests/test_risk_service.py`

添加以下测试:

```python
def test_empty_events_no_crash(self):
    """Empty event list should not crash risk assessment."""
    svc = RiskService()
    assessment = svc.assess({}, {}, [], [], {})
    assert isinstance(assessment, dict)
    assert "overall_risk_score" in assessment

def test_boundary_risk_score_recommendation(self):
    """Test recommendation at boundary risk score values."""
    svc = RiskService()
    # Test with minimal input that would produce low risk
    assessment = svc.assess(
        {"revenue_series": [100, 110, 120], "gross_margin_trend": "improving", "ocf_vs_net_income": {"ratio": 1.1}},
        {},
        [],
        [],
        {"blended": {"base": 100}},
    )
    assert assessment["recommendation"] in {"buy", "watch", "avoid", "research_more"}

def test_missing_all_inputs_returns_research_more(self):
    """Completely empty inputs should return research_more as safe default."""
    svc = RiskService()
    assessment = svc.assess({}, {}, [], [], {})
    assert assessment["recommendation"] in {"research_more", "watch"}
```

### 8.3 扩展 `tests/test_workflow.py`

添加以下测试:

```python
def test_workflow_survives_provider_failure(session):
    """Workflow should complete even if a provider raises an exception."""
    # This test verifies that after Task 2 changes, the workflow
    # handles node failures gracefully.
    from app.graph.state import create_initial_state
    from app.graph.workflow import build_research_workflow

    state = create_initial_state("NONEXIST", "Does Not Exist Corp")
    graph = build_research_workflow(session)
    result = graph.invoke(state)
    # Should not raise; may have errors but should have final_report
    assert "errors" in result
    assert isinstance(result.get("final_report", ""), str)
```

---

## 任务 9：运行测试验证

完成以上所有修改后，执行:

```bash
cd /Users/jacky/Ai-code/investment-mas
python -m pytest tests/ -v --tb=short
```

确保:
1. 所有既有测试继续通过
2. 所有新增测试通过
3. 无 import 错误

---

## 执行优先级总结

| 顺序 | 任务 | 优先级 | 影响 |
|------|------|--------|------|
| 1 | 任务 1: ResearchState total=False + report_json | P0 | 阻塞 MVP 验收 |
| 2 | 任务 2: 6 个节点容错 | P0 | 阻塞 MVP 验收 |
| 3 | 任务 3: report_json 传递 | P0 | 与任务 2.5 同步 |
| 4 | 任务 4: ValuationAgent 接入 company_profile | P1 | 设计合规 |
| 5 | 任务 5: config.py 补充配置项 | P1 | 设计合规 |
| 6 | 任务 6: 补充数据库索引迁移 | P1 | 查询性能 |
| 7 | 任务 7: 5 个新测试文件 | P0 | 测试覆盖 |
| 8 | 任务 8: 扩展现有测试 | P1 | 边界覆盖 |
| 9 | 任务 9: 运行测试验证 | P0 | 最终验证 |

---

## 约束与注意事项

1. 所有修改必须保持类型注解
2. 不要改变 Provider 抽象接口签名（除 ValuationService.run 新增可选参数外）
3. 不要删除或修改现有通过的测试
4. 新增 alembic migration 的 revision 链必须正确 (`down_revision = "20260419_0001"`)
5. 测试文件中的 `session` fixture 来自 `tests/conftest.py`，使用 SQLite in-memory
6. 确保 `state.get()` 替代 `state[]` 的改动不影响正常路径的返回值
