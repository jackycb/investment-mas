# Investment Research MAS 系统架构文档（面向后续 AI Coding）

## 1. 文档目的

本文档用于作为后续 **Codex / Claude Code / DeepSeek v4 Pro** 的统一设计依据，目标是：

1. 明确系统边界与目标能力，避免 AI Coding 跑偏；
2. 统一工作流、数据库、Agent、LLM 调用、检索层、UI 与交互闭环设计；
3. 基于当前已打通的 MAS 系统做**增量优化**，而不是推倒重写；
4. 为后续开发、验证、回归测试、部署和多轮问答提供清晰蓝图。

---

## 2. 现状与问题定义

### 2.1 当前已有能力（v4 已实现，2026-10-17）

v4 Phase 4 Optimization 在 v3 基础上完成性能优化、技能系统和可观测性升级。

**v4 新增能力（Phase 4，2026-10-17）：**
- ✅ **Skill 技能系统** — 5 个可复用技能（market_data / financial_metrics / financial_report_ingestion / evidence_retrieval / valuation），统一 compute() 接口 + 缓存 + 重试 + 遥测
- ✅ **Compute Cache Service** — 统一缓存层，TTL 过期，确定性 cache key，命中率追踪（market=77%, metrics=50%, valuation=50%）
- ✅ **Parallel DAG 执行** — 4 路 Specialist 真正并行执行（asyncio.gather），独立 SQLAlchemy Session，无竞态
- ✅ **Performance Tracker** — 壁钟计时、LLM token 去重计数、ticker 归一化、REST API 端点
- ✅ **LLM 提供商升级** — API 端点迁移至 `token-plan.maas.qianwenaiapi.com`，模型升级至 auto（支持 qwen3.8-max / qwen3.8-flash / qwen3.7-plus / deepseek-v4.1-flash / deepseek-v4-pro / glm-5.3）
- ✅ **Yahoo Finance 去重** — PeerAnalystNode 和 FinancialService.compare_peers 共享 market_data skill 缓存，消除重复 5.6s 网络请求
- ✅ **Lazy Vector Store** — NullVectorStoreProvider 跳过 ChromaDB 冷初始化（节省 736ms）
- ✅ **数据指纹缓存失效** — FinancialMetrics 基于 `created_at` 的数据指纹，新财报自动触发缓存失效
- ✅ **NodeExecution 持久化** — 每次节点执行记录到 DB，支持历史性能分析

**v3 核心能力（保留）：**
- ✅ 基于 LangGraph 的 Expert-Led 研究工作流（11 节点，4 并行分支）
- ✅ ResearchPlanner LLM 规划研究范围、peer、数据需求（含确定性 fallback）
- ✅ DataAvailabilityChecker 确定性数据就绪度检查（吸收原 ContextLoader + FilingCoverageChecker）
- ✅ DataFetcher 按需获取数据（吸收原 PeerFilingIngester）
  - Peer 年报使用 NullVectorStoreProvider，跳过 ChromaDB，仅存 PostgreSQL
- ✅ CompanyProfiler plan-aware，默认 skip_ingestion=True
- ✅ BusinessAnalyst 吸收原 CultureAnalyst，输出 business_analysis + segment_analysis + culture_analysis
- ✅ PeerAnalyst 使用 ResearchPlanner 推荐的 peer 列表 + Yahoo Finance TTM 数据
- ✅ NewsAnalyst（原 NewsCollector 重命名）尊重新闻缓存新鲜度
- ✅ SynthesisReviewer 两阶段：确定性质量门 + LLM 综合报告（取代 InvestmentExpert + ReportCoverageChecker）
- ✅ 所有 Specialist 读取 research_plan.sections 的 skip / focus_areas / depth
- ✅ Section-level caching（7 天过期），DataAvailabilityChecker 恢复缓存到 state
- ✅ SSE 流式输出所有节点进度
- ✅ Word (.docx) 报告导出

**已有基础能力：**
- Peer 发现由 ResearchPlanner LLM 推荐（1-3 个同行业直接竞争对手）+ Yahoo Finance TTM 统一数据源
- Retrieval policy 按分析领域配置 source priority
- prewarm / refresh-peers / rerun-section / followup / feedback API 端点
- Peer metric comparison table 集成到报告 markdown（公司名显示、收入以百万计+货币单位、目标公司加粗置顶）
- 财报优先从本地目录获取，缺失时可回退数据库
- 新闻与公开信息支持抓取
- Filing pipeline: discovery → download → parse → chunk → embed → vector store → retrieval
- CompanyProfiler topic 检索（business_model, revenue_model, key_products, key_regions, competition, management, corporate_culture, risk_factors）
- RiskAnalyst 结合 filing chunk 做辅助证据检索
- 本地保存：公司年报原文件、向量数据库、公司基本信息、三张财务报表
- 财务分析指标（PE/PB/ROE/ROIC/Gross Margin/Net Margin）、价值投资估值（买入点/卖出点）
- Vector store 支持 ChromaDB（chroma）和 NullVectorStoreProvider（none）两种模式
- 当 ChromaDB 不可用时，CompanyProfileRAGService 自动回退到 PostgreSQL filing_chunks 关键词检索

### 2.2 当前主要问题（v3 进展标注）

标注说明：✅ 已解决 / ⚡ 已缓解 / ❌ 待解决

1. **商业分析证据覆盖不够** ⚡
   - ⚡ CompanyProfiler 已具备文档检索能力，BusinessAnalyst 读取 focus_areas 做针对性分析
   - ⚡ SynthesisReviewer Phase 1 检查 section 内容完整性
   - ❌ 尚未形成”证据覆盖率强制补全”自动循环机制

2. **公司业务拆解不够细** ⚡
   - ⚡ BusinessAnalyst 已支持 segment_analysis 输出
   - ❌ 对集团公司的板块收入/利润占比提取仍依赖 LLM 从 filing 中总结

3. **企业文化分析过浅** ✅
   - ✅ BusinessAnalyst 吸收 CultureAnalyst，输出 culture_analysis
   - ✅ 覆盖管理层核心成员、稳定性、激励机制、薪酬/股权激励、资本配置与治理风格

4. **财务、估值、业务三者未闭环** ⚡
   - ⚡ ValuationAgent 依赖 financial_summary + peer_comparison
   - ⚡ RiskAnalyst 依赖 valuation + event_list + red_flags
   - ❌ 财务分析结果尚未系统性反哺商业模式判断

5. **风险评估偏弱** ⚡
   - ⚡ RiskAnalyst 已使用 filing risk factors + news events + financial red flags
   - ⚡ 输出包含 monitoring_signals + what_would_change_my_mind
   - ❌ 风险证据链追溯到具体 chunk 的 UI 展示未完成

6. **系统像”一次性报告生成器”，不像”持续投研助手”** ⚡
   - ✅ followup QA + feedback refinement + rerun-section API 已实现
   - ❌ 完整的多轮问答 UI 和反馈驱动 section 重跑 UI 未完成

---

## 3. 目标能力（升级版 MAS）

优化后的系统应具备以下能力：

### 3.1 报告生成能力
系统需生成高质量投资研究报告，覆盖：

1. 商业模式
2. 市场空间
3. 竞争格局
4. 企业文化与治理
5. 财务分析
6. 估值分析
7. 风险评估
8. 最终建议

### 3.2 集团公司专项能力
若目标公司为集团型或多业务板块公司，应尽量输出：

- 主营业务拆解
- 各业务板块收入/利润/占比（若本地数据或年报支持）
- 各业务板块市场空间
- 各业务板块竞争格局
- 各板块主要驱动因素与主要风险

### 3.3 企业文化/治理专项能力
尽量覆盖：

- 管理层核心成员
- 管理层稳定性
- 激励约束机制
- 薪酬与股权激励
- 公司治理与资本配置倾向

### 3.4 持续问答能力
研究报告生成后，用户可以继续追问，例如：

- 这个结论依据哪几段年报？
- 重点展开分析某个业务板块
- 重新看一下 ROIC 和估值
- 结合最新新闻，更新风险结论
- 为什么把某家公司归为核心竞争对手？

系统回答时必须能联合使用：

- 本地 filing 文档和 chunk 检索
- 本地向量数据库
- 本地财务数据库
- 本地公司基础信息
- Web searching 结果

### 3.5 用户反馈驱动能力
支持用户针对报告反馈，例如：

- 商业分析太浅
- 竞争格局不够完整
- 风险分析太空泛
- 希望重点展开某部分
- 某结论证据不够

系统应支持：
- section 级重跑
- 证据补全
- follow-up 会话增强

---

## 4. 系统总体架构

### 4.1 分层架构

```text
[User Interface / Frontend]
        |
        v
[FastAPI API Layer]
        |
        v
[LangGraph Orchestration Layer]
        |
        +--> Research Graph (Expert-Led Planning + Targeted Execution)
        +--> Follow-up QA Graph
        +--> Feedback Refinement Graph
        |
        v
[Agent / Node Layer]
        |
        +--> ResearchPlanner (LLM: scope, peers, data requirements)
        +--> DataAvailabilityChecker (deterministic: check local readiness)
        +--> DataFetcher (targeted: only fetch what's missing)
        +--> CompanyProfiler / BusinessAnalyst / PeerAnalyst
        +--> NewsAnalyst / FinancialAnalyst
        +--> ValuationAgent / RiskAnalyst
        +--> SynthesisReviewer (LLM: final report + quality gate)
        +--> FollowupQAAgent / FeedbackRefiner
        |
        v
[Service Layer]
        |
        +--> filing_service
        +--> retrieval_service
        +--> company_profile_rag_service
        +--> segment_analysis_service
        +--> culture_governance_service
        +--> peer_intelligence_service
        +--> financial_service
        +--> valuation_service
        +--> risk_service
        +--> report_service
        +--> section_cache_service
        |
        v
[Provider Layer]
        |
        +--> FilingDiscoveryProvider (SEC EDGAR / HKEXnews / CNINFO)
        +--> FilingDownloadProvider
        +--> VectorStoreProvider (ChromaVectorStoreProvider / NullVectorStoreProvider)
        +--> StructuredFinancialProvider
        +--> YahooFinanceProvider (yfinance TTM — peer comparison 统一数据源)
        +--> NewsProvider
        +--> CompanyInfoProvider
        +--> WebSearchProvider
        +--> LLMProvider (DashScope / DeepSeek)
        +--> EmbeddingProvider (LocalHashEmbeddingProvider)
        |
        v
[Persistence Layer]
        |
        +--> PostgreSQL (companies, filings, filing_chunks, financials, reports, ...)
        +--> Local Document Store (data/company_docs/)
        +--> Local Vector Store (data/vector_store/ — ChromaDB SQLite, optional)
```

---

## 5. 工作流设计

### 5.0 历史版本 — Staged Parallel（v2，已淘汰）

上一版工作流采用分阶段并行执行（13 节点，4 并行分支），问题在于：
- 固定管线，无论什么公司都做相同工作
- PeerFilingIngester 总是下载 peer 年报，即使本地已有或不需要
- ContextLoader / FilingCoverageChecker 做的检查不够精准，后续 specialist 仍然自己再查一次
- InvestmentExpert 只在最后出现，无法影响前序节点的工作范围

v2 旧节点（ContextLoader, FilingCoverageChecker, CultureAnalyst, PeerFilingIngester, InvestmentExpert, ReportCoverageChecker）的源文件仍保留在 `app/graph/nodes/` 中以便向后兼容，但不再被 v3 工作流使用。

---

### 5.1 当前工作流 — Expert-Led Planning + Targeted Execution（v3，已实现）

> **状态：已实现并通过端到端验证（2026-05-04）。**
> 实现文件：`app/graph/workflow.py`（LangGraph DAG）、`app/services/research_runner.py`（顺序执行 + SSE 流式输出）。

核心思想：**让投研专家（LLM）先做计划，再按需取数据，最后让 specialist 各自深入分析。**

```text
START
  → ResearchPlanner                    # Stage 1: LLM 规划研究范围、peer、数据需求
  → DataAvailabilityChecker            # Stage 2: 确定性检查本地数据就绪度
  → DataFetcher                        # Stage 3: 只下载缺失的数据（目标年报 / peer 年报 / 新闻）
  → CompanyProfiler                    # Stage 4: 生成公司画像（skip_ingestion=True）
  → Parallel Specialists:              # Stage 5: 4 路并行分析
      ├── BusinessAnalyst              #   商业分析 + 文化治理
      ├── FinancialAnalyst             #   财务分析 + 红旗 + peer 比较（Yahoo Finance TTM）
      ├── NewsAnalyst                  #   新闻 + 事件抽取
      └── PeerAnalyst                  #   peer 指标对比（Yahoo Finance TTM 统一数据源）
  → ValuationAgent                     # Stage 6: 估值（需要 financial_summary, peer_comparison）
  → RiskAnalyst                        # Stage 7: 风险（需要 valuation, events, red_flags）
  → SynthesisReviewer                  # Stage 8: 质量检查 + LLM 综合报告
→ END
```

**11 个节点**（从 13 个减少，4 路并行分支）。

---

### 5.1.1 编排原则

1. **ResearchPlanner 先行**：LLM 根据 ticker + company_name 输出研究计划（`research_plan`），包括：
   - 哪些 section 需要做（可标记 `skip: true`，如某些公司不需要 peer 分析）
   - 每个 section 的 `focus_areas`（重点方向）和 `depth`（分析深度）
   - 推荐的 1-3 个同行业直接竞争对手（含 rationale），由 LLM 判断，后处理过滤无效条目并截断至 3 个
   - 数据需求清单：是否需要目标年报、peer 年报、peer 财务、新闻等
   - 3-5 个 research questions（本报告应回答的核心问题）

2. **DataAvailabilityChecker 做清查**：不做 LLM 调用，只做 DB/filesystem 查询：
   - 目标公司 filing/chunks/financial/profile 是否已存在
   - 每个 peer 的 filing/financial 是否已存在
   - 新闻是否有缓存、多久前抓的
   - Section cache 是否新鲜（7 天过期）
   - 输出 `data_availability` dict + `fetch_needed` dict

3. **DataFetcher 按需取数据**：只获取 `fetch_needed` 里列出的数据：
   - 如果目标公司年报已有，不重新下载
   - 如果某个 peer 已有年报，不重新下载
   - 如果 peer 不在计划里，不下载
   - 如果新闻缓存新鲜（<1 天），不重新抓
   - 有超时预算，避免无限制下载

4. **CompanyProfiler 纯合成**：数据已就绪，`skip_ingestion=True`，只做 evidence 检索 + LLM 合成

5. **4 路并行 Specialist**：每个 specialist 读取 `research_plan.sections[self_section]` 里的 `focus_areas` 和 `depth`：
   - 如果 `skip: true`，立即返回空结果
   - 如果 `depth: “standard”`，用默认 top_k
   - 如果 `depth: “detailed”`，增加 top_k 和 evidence 查询
   - BusinessAnalyst 同时负责 `culture_analysis`（原 CultureAnalyst 合并进来）

6. **ValuationAgent / RiskAnalyst** 串行：依赖前序 specialist 输出

7. **SynthesisReviewer 两阶段**：
   - **Phase 1（确定性质量门）**：检查每个 section 是否有内容，集团公司是否有分板块分析等
   - **Phase 2（LLM 综合）**：调用 `ReportService.build()` 生成 Markdown + JSON + rating
   - 将 `research_plan.research_questions` 嵌入 report_json，确保报告回答了计划中的关键问题
   - 输出 `report_coverage`（取代原 ReportCoverageChecker）

---

### 5.1.2 节点变更对照表

| 旧节点（v2） | 操作 | 新节点（v3） |
|---|---|---|
| ContextLoader | 移除 → 功能拆入 DataAvailabilityChecker | DataAvailabilityChecker |
| CompanyProfiler | 修改 → 永远 skip_ingestion=True | CompanyProfiler |
| FilingCoverageChecker | 移除 → coverage 检查移入 DataAvailabilityChecker | — |
| BusinessAnalyst | 修改 → 吸收 CultureAnalyst，读取 research_plan | BusinessAnalyst |
| CultureAnalyst | 移除 → 合并入 BusinessAnalyst | — |
| PeerAnalyzer | 重命名 → PeerAnalyst，使用 plan 的 peer 列表 + Yahoo Finance TTM | PeerAnalyst |
| PeerFilingIngester | 移除 → 功能移入 DataFetcher | DataFetcher |
| NewsCollector | 重命名 → NewsAnalyst，尊重新闻缓存新鲜度 | NewsAnalyst |
| FinancialAnalyst | 修改 → peer 比较使用 Yahoo Finance TTM 统一数据源 | FinancialAnalyst |
| ValuationAgent | 保持不变 | ValuationAgent |
| RiskAnalyst | 保持不变 | RiskAnalyst |
| InvestmentExpert | 拆分 → ResearchPlanner（开头）+ SynthesisReviewer（结尾） | ResearchPlanner + SynthesisReviewer |
| ReportCoverageChecker | 移除 → 质量门合并入 SynthesisReviewer | — |

---

### 5.1.3 ResearchState 变更

新增字段：

```python
research_plan: dict           # ResearchPlanner 输出的研究计划
data_availability: dict       # DataAvailabilityChecker 输出的数据就绪度
fetch_results: dict           # DataFetcher 输出的获取结果
```

保留 `filing_coverage: dict`（由 DataAvailabilityChecker 从 data_availability 中映射填充，保持向后兼容）。

`errors` 和 `warnings` 继续使用 `Annotated[list[str], _merge_lists]` reducer 支持并行分支。

---

### 5.1.4 ResearchPlanner LLM Prompt 设计

```text
System: You are an investment research director. Given a company ticker and name,
produce a structured JSON research plan.

Output fields:
- sections: dict of section_name -> {priority, focus_areas, skip, depth}
  Sections: business_analysis, culture_governance, peer_analysis,
  financial_analysis, valuation, risk_analysis, news_analysis
- peer_candidates: list of {ticker, company_name, rationale} — 1-3 direct industry peers
  Use market-prefix format: US.AMD, HK.09988, etc. Only genuine competitors or comparable businesses
- data_requirements: {target_filing, target_financials,
  peer_filings: [tickers], peer_financials: [tickers],
  recent_news, company_basic_info}
- research_questions: 3-5 key questions this report should answer

Priorities: “high” (must have), “medium” (nice to have), “low” (skip if constrained)
If peer analysis is not meaningful, set peer_analysis.skip = true.
```

**Fallback（LLM 不可用时）**：使用 `PeerIntelligenceService.discover_top_peers_fast()` + 默认 section config 生成确定性计划。

---

### 5.1.5 DataAvailabilityChecker 输出格式

```python
{
    “company_id”: 42,
    “target”: {
        “filings”: {“status”: “available|missing”, “effective_count”: 3, “latest_year”: 2024},
        “filing_chunks”: {“status”: “available|missing”, “count”: 487},
        “financial_data”: {“status”: “available|missing”, “years”: [2020,...], “source”: “local_dir|database”},
        “company_profile”: {“status”: “cached|expired|missing”, “age_days”: 3},
        “basic_info”: {“status”: “available|missing”},
    },
    “peers”: {
        “US.AMD”: {“filings”: {“status”: “available”}, “financial_data”: {“status”: “available”}},
        “US.INTC”: {“filings”: {“status”: “missing”}, “financial_data”: {“status”: “missing”}},
    },
    “news”: {“status”: “fresh|stale|missing”, “age_days”: 13},
    “section_cache”: {“company_profile”: {“status”: “fresh”, “age_days”: 3}, ...},
    “fetch_needed”: {
        “target_filings”: false,
        “peer_filings”: [“US.INTC”],
        “news”: true,
    },
}
```

---

### 5.1.6 DataFetcher 逻辑

> **实现文件**：`app/graph/nodes/data_fetcher.py`

```text
for each item in fetch_needed:
    if target_filings:
        FilingService().ingest_company_filings(company, years=1, filing_types={“annual_report”})
        # 使用默认 vector store（ChromaDB 或 NullVectorStoreProvider，取决于配置）
    for each peer_ticker in peer_filings:
        FilingService(vector_store=NullVectorStoreProvider()).ingest_company_filings(...)
        # Peer 年报使用 NullVectorStoreProvider，跳过 ChromaDB，仅存 PostgreSQL
        # 这避免了 ChromaDB 大文件（1.7GB+ SQLite）导致的性能问题
    if news:
        NewsService().collect_news(session, ticker, company_name, [])
```

不获取 plan 里没有的数据。Peer 年报的 chunk 存储在 PostgreSQL `filing_chunks` 表中，PeerAnalyst 通过 SQL 查询提取指标。

---

### 5.1.7 Specialist 消费 research_plan 的模式

```python
def __call__(self, state):
    plan = state.get(“research_plan”, {})
    section_plan = plan.get(“sections”, {}).get(“business_analysis”, {})

    if section_plan.get(“skip”):
        return {“business_analysis”: {}, “segment_analysis”: {}, “culture_analysis”: {}}

    focus_areas = section_plan.get(“focus_areas”, [])
    # 将 focus_areas 传给 service，用于 scope evidence retrieval queries
    result = self.service.analyze(..., focus_areas=focus_areas)
```

---

### 5.1.8 SynthesisReviewer 逻辑

```text
Phase 1 (deterministic quality gate):
    检查每个 section 是否有内容
    检查集团公司标记一致性
    检查 valuation 是否有 base value
    检查 risk 是否有 key_risks
    → output report_coverage dict

Phase 2 (LLM synthesis):
    调用 ReportService.build(state)
    将 research_plan.research_questions 注入 report_json
    附加 plan execution summary（planned vs actual）
    → output final_report, final_rating, report_json
```

---

### 5.1.9 API 端点

现有端点全部保留，新增：
- `GET /research/{ticker}/plan` — 返回最近一次研究的 research_plan
- `POST /research/prewarm` — 增加可选 `peers: list[str]` 参数，同时预热 peer 年报

---

### 5.2 报告后的 Follow-up QA 工作流

（保持不变）

---

### 5.2 报告后的 Follow-up QA 工作流

建议新增一个 **持续问答图**：

```text
START
-> QueryClassifier
-> QueryPlanner
-> MultiSourceRetriever
-> AnswerComposer
-> EvidenceFormatter
-> END
```

### 节点说明

#### QueryClassifier
判断用户问题属于：
- 报告追问
- section 深挖
- 某结论证据追溯
- 财务问题
- 估值问题
- 新闻/事件问题
- 最新信息更新

#### QueryPlanner
确定查询策略：
- 查结构化数据库
- 查 filing chunk
- 查新闻事件
- 查 web search
- 组合查询

#### MultiSourceRetriever
联合检索：
- local structured DB
- vector store
- local docs
- web search

#### AnswerComposer
基于 evidence 回答问题，要求：
- 先结论
- 再证据
- 再不确定性

#### EvidenceFormatter
将引用证据整理为：
- 文档名
- section
- page
- chunk 引用
- 时间戳
- 来源

---

### 5.3 用户反馈驱动工作流

建议新增 **FeedbackRefinement Graph**：

```text
START
-> FeedbackClassifier
-> ImpactedSectionDetector
-> SectionRefinementPlanner
-> TargetedRetriever
-> SectionRegenerator
-> ReportPatchMerger
-> END
```

### 适用反馈示例
- 商业分析太浅
- 竞争格局不够完整
- 企业文化不可信
- 重新分析估值
- 风险部分没有抓到重点

### 核心思路
不是整份报告重跑，而是：

1. 判断受影响 section
2. 只补查证据
3. 重生成 해당 section
4. merge 回原报告

---

## 6. Agent / Node 定义（v3）

> 下述节点定义对应 v3 工作流（5.1 节）。旧的 ContextLoader、FilingCoverageChecker、
> CultureAnalyst、PeerFilingIngester、InvestmentExpert、ReportCoverageChecker 已被
> 合并或替代，不再单独存在。

### 6.1 ResearchPlanner（新增，已实现）
类型：LLM 节点
实现文件：`app/graph/nodes/research_planner.py`

输入：
- ticker
- company_name
- market（可选）

输出：
- research_plan（写入 state）

research_plan 包含：
- sections: dict，每个 section → {priority, focus_areas, skip, depth}
- peer_candidates: list of {ticker, company_name, rationale}（1-3 个同行业直接竞争对手，后处理验证+截断）
- data_requirements: {target_filing, target_financials, peer_filings, peer_financials, recent_news, company_basic_info}
- research_questions: 3-5 个核心问题

Fallback（LLM 不可用）：`PeerIntelligenceService.discover_top_peers_fast()` + 默认 section config。

---

### 6.2 DataAvailabilityChecker（新增，已实现）
类型：确定性节点（无 LLM）
实现文件：`app/graph/nodes/data_availability_checker.py`

输入：
- research_plan
- DB session

输出：
- data_availability（详细就绪度，见 5.1.5）
- fetch_needed（哪些数据需要获取）
- filing_coverage（向后兼容旧字段）

吸收原 ContextLoader 的数据检查逻辑 + 原 FilingCoverageChecker 的 filing 检查逻辑。

---

### 6.3 DataFetcher（新增，已实现）
类型：确定性节点（无 LLM）
实现文件：`app/graph/nodes/data_fetcher.py`

输入：
- fetch_needed（来自 DataAvailabilityChecker）
- research_plan.peer_candidates

输出：
- fetch_results（包含 target_filings_fetched, peer_filings_fetched, news_fetched, fetch_errors）

逻辑：
- 按 fetch_needed 列表逐项获取，不获取 plan 外的数据
- 目标年报：`self.filing_service.ingest_company_filings(years=1, filing_types={"annual_report"})`
  - 使用默认 vector store（完整 pipeline，含 ChromaDB 或 NullVectorStoreProvider）
- Peer 年报：`self._peer_filing_service.ingest_company_filings(...)`
  - 使用 `FilingService(vector_store=NullVectorStoreProvider())`，跳过 ChromaDB upsert
  - Chunk 仅存 PostgreSQL `filing_chunks` 表，PeerAnalyst 通过 SQL 提取指标
  - 这避免了 ChromaDB 大文件（1.7GB+ SQLite）导致的 IO 瓶颈
- 新闻：`NewsService().collect_news(session, ticker, company_name, [])`

吸收原 PeerFilingIngester 的功能。

---

### 6.4 CompanyProfiler
类型：LLM 节点

输入：
- evidence_bundle（数据已就绪，skip_ingestion=True）
- company basic info

输出：
- company_profile

核心字段：
- business_model
- revenue_model
- key_products
- key_regions
- key_customers
- high_level_competition_landscape
- evidence_refs

---

### 6.5 BusinessAnalyst（吸收原 CultureAnalyst）
类型：LLM 节点

输入：
- company_profile
- filing evidence
- segment / market / competition evidence
- management / governance / incentive evidence
- web searching evidence
- research_plan.sections.business_analysis（focus_areas, depth）

输出：
- business_analysis
- segment_analysis
- market_space_analysis
- competition_analysis
- culture_analysis（原 CultureAnalyst 的输出）

关键要求：
- 若是集团公司，必须尝试拆解主要业务板块
- 若缺业务占比，不能编造，但要明确已识别/缺失的板块
- culture_analysis 至少覆盖：管理层核心成员、稳定性、激励机制、薪酬/股权激励、资本配置与治理风格
- 如果 `research_plan.sections.business_analysis.skip == true`，立即返回空结果

---

### 6.6 PeerAnalyst（原 PeerAnalyzer 重命名）
类型：LLM 节点

输入：
- research_plan.peer_candidates（LLM 推荐的 1-3 个同行业直接竞争对手）
- Yahoo Finance TTM 数据（目标公司 + 所有 peers，统一数据源）
- filing competition evidence
- web search evidence

输出：
- peer_analysis
- peer_metric_snapshots（含 Yahoo Finance TTM 指标快照）

要求：
- 使用 plan 里的 peer 列表，不自行发现新 peer，最多 3 个
- 对每个 peer：core / secondary / valuation 分类 + rationale + evidence
- 通过 `fetch_yahoo_peer_financials()` 统一获取目标公司和 peer 的 revenue / margins / ROE / ROIC 等 TTM 指标
- 目标公司包含在 Yahoo 请求中，确保比较数据来源一致
- Yahoo 无法获取的 peer 回退到 `PeerIntelligenceService.load_peer_metrics()` 本地数据
- 报告中的 peer metric comparison table：公司名称显示（非 ticker）、收入以百万计+货币单位、目标公司加粗且置顶
- 如果 `research_plan.sections.peer_analysis.skip == true`，立即返回空结果

---

### 6.7 NewsAnalyst（原 NewsCollector 重命名）
类型：混合节点（抓取 + 可选 LLM 增强）

输入：
- ticker
- research_plan.peer_candidates
- data_availability.news（新鲜度判断）

输出：
- company_news
- peer_news
- event_list
- recent_external_signals

要求：
- 尊重新闻缓存新鲜度（<1 天不重抓）
- 重点产出结构化事件
- 如果 `research_plan.sections.news_analysis.skip == true`，立即返回空结果

---

### 6.8 FinancialAnalyst
类型：混合节点（结构化数据 + LLM 合成）

输入：
- structured financial statements
- base info
- research_plan.peer_candidates（1-3 个同行业 peers）
- research_plan.sections.financial_analysis

输出：
- financial_analysis
- financial_summary（key metrics snapshot，供 ValuationAgent 使用）
- financial_red_flags
- peer_comparison（通过 Yahoo Finance TTM 统一数据源获取目标公司和 peers 的指标）

重点指标：
- gross margin / net margin / ROE / ROIC
- revenue growth / OCF vs NI / cash conversion / capital intensity

Peer 比较数据源：
- `fetch_yahoo_peer_financials()` 统一获取目标公司和 peers 的 TTM 数据
- 确保比较指标来自同一数据源，避免混合不同来源导致的不一致

---

### 6.9 ValuationAgent
类型：LLM 节点

输入：
- company basic info
- financial_summary（来自 FinancialAnalyst）
- peer_comparison（来自 PeerAnalyst / FinancialAnalyst）
- current market cap / price / shares outstanding
- value investing heuristics

输出：
- valuation_analysis

需要覆盖：
- value-style valuation
- growth-style valuation
- buy point / sell point
- scenario range
- key assumptions

---

### 6.10 RiskAnalyst
类型：LLM 节点

输入：
- filing risk factors
- event_list（来自 NewsAnalyst）
- financial_red_flags（来自 FinancialAnalyst）
- culture_analysis（来自 BusinessAnalyst）
- valuation_analysis（来自 ValuationAgent）

输出：
- risk_analysis

需要覆盖：
- filing-based disclosed risks
- financial risks
- competition risks
- governance risks
- valuation risks
- monitoring signals
- what would change the conclusion

---

### 6.11 SynthesisReviewer（新增，已实现，替代 InvestmentExpert + ReportCoverageChecker）
类型：混合节点（确定性质量门 + LLM 综合）
实现文件：`app/graph/nodes/synthesis_reviewer.py`

输入：
- 所有前序结构化结果
- research_plan（含 research_questions）
- evidence_refs

输出：
- report_json
- report_markdown
- final_rating
- report_coverage（取代原 ReportCoverageChecker）
- followup suggestions

两阶段逻辑：
- Phase 1（确定性质量门）：检查每个 section 是否有内容、集团公司标记一致性、valuation base value、risk key_risks
- Phase 2（LLM 综合）：调用 `ReportService.build(state)` 生成报告，将 research_questions 注入 report_json

---

### 6.12 FollowupQAAgent（未变）
输入：
- user question
- current report
- session context
- retrieved evidence

输出：
- answer
- evidence_refs
- answer_confidence
- unresolved_questions

---

### 6.13 FeedbackRefiner（未变）
输入：
- user feedback
- original report
- section map
- retrieval policy

输出：
- updated sections
- patch report
- feedback resolution note

---

## 7. Retrieval 策略设计

### 7.1 检索原则
所有分析都应遵循：

1. **先查本地结构化数据**
2. **再查 filing chunks / vector store**
3. **最后补 web search**

即：

```text
Structured DB -> Filing Vector Store -> Web Search
```

这样可以避免：
- 明明本地有财务数据，却让 LLM重新总结
- 明明年报里有结论，却用 web 搜索替代正式披露

---

### 7.2 Query Planning
建议在系统中引入 `QueryPlanner` / `EvidencePlanner`，为不同 section 规定检索优先级。

#### business_model
- filing chunks 优先
- web search 补充

#### segment_analysis
- filing chunks 优先
- local structured segment table（若存在）优先于 web

#### culture_and_governance
- filing chunks
- compensation / management tables（若存在）
- 新闻事件补充

#### financial_analysis
- structured DB 优先
- filing 数值仅作证据补充

#### valuation_analysis
- structured DB + current company basic info 优先
- filing text 只补充 growth assumptions

#### risk_analysis
- filing risk_factors
- structured red flags
- news event_list
- web latest updates

---

## 8. 数据库设计（目标版）

在现有表基础上，建议新增以下表：

### 8.1 company_segment_profiles
用途：
- 保存业务板块分析结果
- 支持集团公司场景

字段建议：
- id
- company_id
- report_session_id
- segment_name
- revenue_share
- profit_share
- business_description
- market_context
- competition_context
- evidence_refs
- created_at

---

### 8.2 management_profiles
用途：
- 保存管理层结构化信息

字段建议：
- id
- company_id
- name
- title
- tenure_years
- joined_date
- background_summary
- stability_signal
- evidence_refs
- created_at

---

### 8.3 incentive_records
用途：
- 保存员工薪酬、股权激励、管理层激励相关信息

字段建议：
- id
- company_id
- fiscal_year
- incentive_type
- target_group
- summary
- amount_or_ratio
- evidence_refs
- created_at

---

### 8.4 evidence_spans
用途：
- 统一保存“结论引用了哪些 chunk / db row / web result”

字段建议：
- id
- entity_type
- entity_id
- source_type
- source_ref
- chunk_id
- page_number
- section
- snippet
- created_at

---

### 8.5 report_sessions
用途：
- 保存一次完整研究会话

字段建议：
- id
- company_id
- session_type
- started_at
- completed_at
- status
- report_json
- report_markdown
- final_rating
- summary
- warnings
- created_at

---

### 8.6 followup_questions
用途：
- 保存报告后的持续追问

字段建议：
- id
- report_session_id
- question
- answer
- evidence_refs
- answer_confidence
- unresolved_questions
- created_at

---

### 8.7 user_feedback
用途：
- 保存用户反馈与局部增强记录

字段建议：
- id
- report_session_id
- section_name
- feedback_type
- feedback_text
- action_taken
- patch_result
- created_at

---

## 9. LLM 调用架构设计

### 9.1 调用原则
LLM 不负责“找事实”，LLM 负责：

- 结构化提炼
- 证据组织
- section-level synthesis
- 风险与估值的解释层
- 对用户问题的 evidence-grounded answer

### 9.1.1 LLM 提供商配置（v4 更新）

**API 端点：**
```
Base URL: https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1
协议: OpenAI-compatible (chat/completions)
```

**可用模型列表：**
| 模型 | 说明 | 适用场景 |
|------|------|----------|
| `auto` | 自动选择（默认） | 通用场景，系统自动路由 |
| `qwen3.8-max` | 通义千问 3.8 Max | 高质量分析、复杂推理 |
| `qwen3.8-flash` | 通义千问 3.8 Flash | 快速响应、低延迟场景 |
| `qwen3.7-plus` | 通义千问 3.7 Plus | 平衡性能与质量 |
| `deepseek-v4.1-flash` | DeepSeek V4.1 Flash | 快速推理 |
| `deepseek-v4-pro` | DeepSeek V4 Pro | 高质量专业分析 |
| `glm-5.3` | GLM 5.3 | 中文理解与生成 |

**环境变量配置：**
```bash
INVESTMENT_MAS_DASHSCOPE_API_KEY="your-api-key"
INVESTMENT_MAS_DASHSCOPE_BASE_URL="https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"
INVESTMENT_MAS_DASHSCOPE_MODEL="auto"
INVESTMENT_MAS_DASHSCOPE_TEMPERATURE="0.2"
INVESTMENT_MAS_DASHSCOPE_TIMEOUT_SECONDS="120.0"
```

> ⚠️ **`auto` 不接受 `temperature` 参数**
> 传入时会返回 `HTTP 400 — "auto does not support parameter(s): temperature"`。
> `AliyunLLMService.chat()` 已针对 `auto` 自动省略该字段，其余模型照常下发 `temperature`。

**配置文件优先级：** 环境变量 > `llm_config.json` > `app/core/config.py` 默认值

**DeepSeek 独立配置（默认关闭）：**
```bash
INVESTMENT_MAS_ENABLE_DEEPSEEK_DIRECT_ROUTING="false"   # 默认 false
INVESTMENT_MAS_DEEPSEEK_API_KEY="your-deepseek-key"
INVESTMENT_MAS_DEEPSEEK_BASE_URL="https://api.deepseek.com"
```
token-plan 网关本身即可服务 `deepseek-v4-pro` / `deepseek-v4.1-flash`，因此**默认所有模型都走上面配置的网关地址**。
仅当 `INVESTMENT_MAS_ENABLE_DEEPSEEK_DIRECT_ROUTING=true` 时，`_resolve_provider(model)` 才会把
以 `deepseek-` 开头的模型改路由到原生 `api.deepseek.com`（此时必须配置 DeepSeek key，否则抛 `RuntimeError`）。

> 历史行为提醒：旧版本按模型名前缀无条件分流，凡是 `deepseek-*` 都会打到 `api.deepseek.com`。
> 迁移到 token-plan 后该行为会导致请求绕过新网关并使用旧密钥，现已改为显式开关。

---

### 9.2 LLM 调用点（v3）

v3 工作流中，LLM 调用点减少但更聚焦。以下按执行顺序列出：

#### A. ResearchPlanner（Stage 1）
输入：ticker + company_name
输出：research_plan JSON（sections, peer_candidates, data_requirements, research_questions）
备注：唯一一个"无 evidence 输入"的 LLM 调用，依赖 LLM 自身的行业知识

#### B. CompanyProfiler（Stage 4）
输入：evidence bundle + company basic info
输出：structured company_profile
备注：数据已由 DataFetcher 就绪，skip_ingestion=True

#### C. BusinessAnalyst（Stage 5 并行）
输入：segment / market / competition / management / governance evidence + research_plan.focus_areas
输出：business_analysis + segment_analysis + culture_analysis
备注：吸收了原 CultureAnalyst 的 LLM 调用

#### D. FinancialAnalyst（Stage 5 并行）
输入：structured financial data + Yahoo Finance TTM peer metrics + research_plan.focus_areas
输出：financial_analysis + financial_summary + red_flags + peer_comparison
备注：结构化数据优先，peer 比较通过 Yahoo Finance TTM 统一数据源

#### E. PeerAnalyst（Stage 5 并行）
输入：Yahoo Finance TTM 数据（目标+peers）+ research_plan.peer_candidates
输出：peer_analysis + peer_metric_snapshots
备注：peer 列表由 ResearchPlanner 确定（1-3 个），Yahoo Finance TTM 为统一数据源

#### F. NewsAnalyst（Stage 5 并行，可选 LLM 增强）
输入：fetched news list
输出：event_list + structured signals
备注：核心是抓取和结构化，LLM 增强为可选

#### G. ValuationAgent（Stage 6）
输入：financial_summary + peer_comparison + company basic info
输出：valuation_analysis
备注：串行，依赖 Stage 5 输出

#### H. RiskAnalyst（Stage 7）
输入：valuation + event_list + red_flags + culture_analysis
输出：risk_analysis
备注：串行，依赖 Stage 5-6 输出

#### I. SynthesisReviewer（Stage 8）
输入：所有前序 section 输出 + research_plan.research_questions
输出：final report JSON + markdown + rating + coverage
备注：Phase 1 为确定性质量门（无 LLM），Phase 2 为 LLM 综合

#### J. FollowupQAAgent（独立图）
输入：question + evidence
输出：grounded answer

**不使用 LLM 的节点**：DataAvailabilityChecker、DataFetcher — 纯确定性逻辑。

---

### 9.3 Prompt 设计原则

所有 prompt 都应统一要求：

1. 只基于输入证据输出
2. 不得引入未提供事实
3. 输出分三层：
   - 结论
   - 依据
   - 影响
4. 若证据不足，明确标注
5. 尽量输出 supporting_evidence_refs

---

## 10. 报告 JSON Schema（目标版）

建议报告结构升级为：

```json
{
  "company_name": "",
  "ticker": "",
  "executive_summary": {
    "core_conclusion": "",
    "rating": "",
    "confidence": 0.0,
    "key_supporting_points": [],
    "key_open_questions": []
  },
  "business_model_analysis": {
    "core_judgement": "",
    "supporting_facts": [],
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "segment_analysis": {
    "is_group_company": false,
    "segments": [],
    "segment_level_open_questions": [],
    "supporting_evidence_refs": []
  },
  "market_space_analysis": {
    "core_judgement": "",
    "market_drivers": [],
    "competition_pressure": [],
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "competition_analysis": {
    "core_peers": [],
    "secondary_peers": [],
    "valuation_peers": [],
    "strengths_vs_peers": [],
    "weaknesses_vs_peers": [],
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "culture_and_governance_analysis": {
    "core_judgement": "",
    "management_summary": "",
    "management_stability_view": "",
    "compensation_and_incentive_view": "",
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "financial_analysis": {
    "core_judgement": "",
    "key_metrics": {},
    "trend_summary": "",
    "red_flags": [],
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "valuation_analysis": {
    "core_judgement": "",
    "value_style": {},
    "growth_style": {},
    "preferred_method": "",
    "key_assumptions": [],
    "buy_point": null,
    "sell_point": null,
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "risk_analysis": {
    "core_judgement": "",
    "filing_risks": [],
    "financial_risks": [],
    "governance_risks": [],
    "valuation_risks": [],
    "monitoring_signals": [],
    "what_would_change_my_mind": [],
    "supporting_evidence_refs": [],
    "open_questions": []
  },
  "final_recommendation": {
    "final_rating": "",
    "reason": "",
    "confidence": 0.0
  },
  "followup_suggestions": [],
  "warnings": [],
  "errors": []
}
```

---

## 11. 用户界面设计

### 11.1 页面结构

#### 页面 1：研究输入页
功能：
- 输入 ticker / company_name / market
- 可选选择：
  - 是否强制重抓 filing
  - 是否更新 web search
  - 是否启用 section 深度分析
  - 是否重点分析某个板块

---

#### 页面 2：报告页
建议分区：

##### 左侧：主报告导航
- Executive Summary
- 商业模式
- 业务板块
- 市场空间
- 竞争格局
- 企业文化/治理
- 财务分析
- 估值分析
- 风险分析
- 最终建议

##### 中间：报告内容
- section 卡片化展示
- 支持折叠/展开 evidence

##### 右侧：证据侧栏
- 引用 chunk
- 来源文档
- section/page
- 新闻原文
- 财务指标来源
- web search 来源

---

#### 页面 3：追问区
支持继续问：

- 这个结论依据是什么？
- 展开说某个 section
- 更新到最新新闻
- 只重做某个 section

---

#### 页面 4：反馈区
提供：
- 👍 有帮助 / 👎 没帮助
- 证据不足
- 太空泛
- 需要更深入
- 指定 section 的反馈

---

### 11.2 UI 交互建议

#### A. 证据抽屉
报告中每个核心判断旁边都可点开“查看证据”：
- filing 名称
- page/section
- chunk 摘要
- 结构化财务来源
- 最近新闻来源

#### B. Section 级重跑按钮
每个 section 提供：
- 重新生成
- 加深分析
- 更新最新信息

#### C. Follow-up 聊天框
用户可继续追问，答案显示：
- 主回答
- 证据引用
- 仍然不确定的点

---

## 12. 增量实施方案（v3 Expert-Led 架构）

### Phase 1：工作流骨架重构 ✅ 已完成（2026-05-04）

改动（已完成）：
- ✅ 新增 ResearchPlanner 节点（`app/graph/nodes/research_planner.py`）
- ✅ 新增 DataAvailabilityChecker 节点（`app/graph/nodes/data_availability_checker.py`）
- ✅ 新增 DataFetcher 节点（`app/graph/nodes/data_fetcher.py`）
- ✅ ResearchState 新增 `research_plan` / `data_availability` / `fetch_results` 字段
- ✅ `workflow.py` 重构为 11 节点 DAG，4 路并行分支
- ✅ ResearchPlanner 确定性 fallback（LLM 不可用时使用 `PeerIntelligenceService.discover_top_peers_fast()`）
- ✅ DataFetcher 使用 `NullVectorStoreProvider` 处理 peer 年报（跳过 ChromaDB）
- ✅ `research_runner.py` 的 `WORKFLOW_STEPS` 和 `_build_node` 适配 v3 所有节点

---

### Phase 2：Specialist 智能化 ✅ 已完成（2026-05-04）

改动（已完成）：
- ✅ BusinessAnalyst 吸收 CultureAnalyst（输出 business_analysis + segment_analysis + culture_analysis）
- ✅ 所有 Specialist 读取 `research_plan.sections[self_section]` 的 focus_areas / depth / skip
- ✅ NewsCollector 重命名为 NewsAnalyst（`NewsAnalystNode` 导出自 `news_collector.py`）
- ✅ PeerAnalyzer 重命名为 PeerAnalyst（`PeerAnalystNode` 导出自 `peer_analyzer.py`）
- ✅ FinancialAnalyst 输出 financial_summary 供 ValuationAgent 使用

---

### Phase 3：SynthesisReviewer + 质量闭环 ✅ 已完成（2026-05-04）

改动（已完成）：
- ✅ 新增 SynthesisReviewer 节点（`app/graph/nodes/synthesis_reviewer.py`）
- ✅ Phase 1 确定性质量门：`ReportCoverageService.check(state)`
- ✅ Phase 2 LLM 综合：`ReportService.build(state, model=self.model)`
- ✅ 新增 API `GET /research/{ticker}/plan`
- ✅ report_coverage 嵌入 synthesis_review 输出

---

### Phase 4：多轮问答与反馈闭环 ⚡ 部分完成

已完成：
- ✅ followup QA API（`POST /research/followup`）
- ✅ feedback refinement API（`POST /research/feedback`）
- ✅ rerun-section API（`POST /research/rerun-section`）
- ✅ 数据表已就绪（report_sessions / followup_questions / user_feedback）

待完成：
- ❌ 完整的多轮问答 UI（追问区 + 反馈区）
- ❌ Follow-up QA 使用 hybrid retrieval（目前仅用 filing chunks + report context）

---

### Phase 5：生产化与体验增强 ❌ 待开始

待完成：
- ❌ 更强 query planner / hybrid retrieval / reranker
- ❌ Telemetry / tracing（per-node latency profiling）
- ❌ UI 交互细化（证据侧栏、section 级重跑按钮、风险监控面板）
- ❌ ChromaDB SQLite 自动 compaction / rebuild 机制
- ❌ Semantic embedding provider 升级（替换 local_hash）

---

## 13. 验收标准

优化后的系统至少应满足：

1. 商业分析不再轻易输出“信息不足”；
2. 如果是集团公司，能尽量按板块拆解业务分析，若证据不足也要明确说明缺口；
3. 企业文化与治理部分能覆盖：
   - 管理层信息
   - 管理层稳定性
   - 薪酬与股权激励（若文档支持）
4. 每个主要结论都能追溯到 evidence refs；
5. 风险分析能显式使用 filing 中的风险因素；
6. 报告生成后，用户可以继续提问；
7. 用户反馈可驱动 section 级增强；
8. 本地数据库、向量库、web searching 三者能协同工作；
9. LangGraph 工作流能支持“主报告生成 + 持续问答 + 反馈重跑”三类任务；
10. UI 可以展示报告、证据、追问、反馈四类核心能力。

---

## 14. LangGraph 流程（v4，已实现）

### 14.1 主报告流程 — Expert-Led Planning + Parallel Targeted Execution ✅

> 实现文件：`app/graph/workflow.py`（LangGraph StateGraph）、`app/services/research_runner.py`（并行执行 + SSE + 性能追踪）

```text
START
  → ResearchPlanner                    # Stage 1: LLM 规划（含确定性 fallback）
  → DataAvailabilityChecker            # Stage 2: 确定性检查（DB/filesystem 查询）
  → DataFetcher                        # Stage 3: 按需获取（peer 用 NullVectorStoreProvider）
                                         ↳ 输出 data_availability 字段（v4 恢复）
  → CompanyProfiler                    # Stage 4: 公司画像（skip_ingestion=True）
  → Parallel Specialists (asyncio):    # Stage 5: 4 路真正并行（独立 SQLAlchemy Session）
      ├── BusinessAnalyst              #   → business_analysis + segment_analysis + culture_analysis
      ├── FinancialAnalyst             #   → financial_summary + financial_red_flags + peer_comparison
      ├── NewsAnalyst                  #   → company_news + peer_news + event_list
      └── PeerAnalyst                  #   → peer_analysis + peer_metric_snapshots
                                         ↳ 使用 market_data skill（缓存共享）
  → ValuationAgent                     # Stage 6: 估值（使用 valuation skill）
  → RiskAnalyst                        # Stage 6: 风险（串行在 ValuationAgent 之后）
  → SynthesisReviewer                  # Stage 7: 质量门 + LLM 综合报告
→ END
```

**v4 关键变更：**
- Stage 5 从 LangGraph 并行边升级为 `asyncio.gather` 真正并行执行
- 每个 Specialist worker 获取独立 SQLAlchemy Session，避免连接共享竞态
- 所有 Specialist 通过 Skill 系统调用（market_data / financial_metrics / valuation），自动缓存
- 壁钟计时：`max(completed) - min(started)` 覆盖所有 span 类型
- LLM token 去重：优先使用 `llm` 专用 span 计数，避免双重计算
- PeerAnalyst 和 FinancialAnalyst 共享 `market_data` skill 缓存，消除重复 Yahoo Finance 请求

11 个节点，4 路并行分支。`research_runner.py` 中以 `WORKFLOW_STEPS` 顺序执行（Stage 5 在 runner 中为顺序但 LangGraph DAG 中为并行边）。

### 14.2 报告后追问流程（未变）
```text
START
-> QueryClassifier
-> QueryPlanner
-> MultiSourceRetriever
-> AnswerComposer
-> EvidenceFormatter
-> END
```

### 14.3 用户反馈增强流程（未变）
```text
START
-> FeedbackClassifier
-> ImpactedSectionDetector
-> SectionRefinementPlanner
-> TargetedRetriever
-> SectionRegenerator
-> ReportPatchMerger
-> END
```

---

## 15. 推荐新增的数据表

1. `company_segment_profiles`
2. `management_profiles`
3. `incentive_records`
4. `evidence_spans`
5. `report_sessions`
6. `followup_questions`
7. `user_feedback`

---

## 16. 推荐新增的 UI 组件

1. 研究输入页
2. 报告页主面板
3. 证据侧栏 / 抽屉
4. Follow-up 问答区
5. 用户反馈区
6. Section 级重跑按钮
7. “更新到最新信息”按钮
8. 业务板块拆分视图
9. 财务趋势图与同行对比卡片
10. 风险监控信号面板

---

## 17. 推荐优先修改的 Prompt 列表（v3）

1. **ResearchPlanner Prompt**（新增 — 见 5.1.4 设计）
2. CompanyProfiler Prompt
3. BusinessAnalyst Prompt（需吸收 CultureAnalyst 职责）
4. PeerAnalyst Prompt（原 PeerAnalyzer，使用 plan 的 peer 列表）
5. FinancialAnalyst Prompt（新增 financial_summary 输出）
6. RiskAnalyst Prompt
7. **SynthesisReviewer Prompt**（新增 — 取代 ReportGenerator/InvestmentExpert）
8. FollowupQAAgent Prompt
9. FeedbackRefiner Prompt

---

## 18. 后续优先改动（v4 已完成后）

v4 Phase 4 优化已全部完成（#1-15）。以下为后续优先项：

1. ~~**新增 ResearchPlanner**~~ ✅
2. ~~**新增 DataAvailabilityChecker**~~ ✅
3. ~~**新增 DataFetcher**~~ ✅
4. ~~**BusinessAnalyst 吸收 CultureAnalyst**~~ ✅
5. ~~**所有 Specialist 读取 research_plan**~~ ✅
6. ~~**新增 SynthesisReviewer**~~ ✅
7. ~~**ResearchState 新增字段**~~ ✅
8. ~~**workflow.py 重构**~~ ✅ 11 节点 DAG，4 路并行 Specialist
9. ~~**新增 API `GET /research/{ticker}/plan`**~~ ✅
10. ~~**Skill 技能系统**~~ ✅ 5 个可复用技能 + 缓存 + 重试 + 遥测
11. ~~**Compute Cache Service**~~ ✅ 统一缓存层，TTL 过期，命中率追踪
12. ~~**Parallel DAG 执行**~~ ✅ asyncio.gather 真正并行，独立 Session
13. ~~**Performance Tracker**~~ ✅ 壁钟计时、LLM token 去重、ticker 归一化、REST API
14. ~~**LLM 提供商升级**~~ ✅ API 端点迁移至 token-plan.maas.qianwenaiapi.com，模型升级至 auto
15. ~~**NodeExecution 持久化**~~ ✅ 每次节点执行记录到 DB
16. **多轮问答 UI + 反馈驱动 section 重跑 UI** — API 已就绪，需前端实现
17. **ChromaDB SQLite compaction / rebuild 自动化** — 当前 1.7GB+ 文件导致 init 极慢
18. **Semantic embedding 升级** — 替换 local_hash 为 text2vec-base-chinese 或 OpenAI embedding
19. **Hybrid retrieval + reranker** — 提升 follow-up QA 质量
20. **UI 证据侧栏** — 报告中每个结论可点开查看 filing chunk / page / section 引用
21. **缓存命中率监控与 TTL 调优** — 当前 market=77%, metrics=50%, valuation=50%
22. **冷路径优化** — Market data 冷请求 18.7s 仍是瓶颈，考虑预取或后台预热

---

## 19. Phase 4 优化总结（2026-10-17）

### 19.1 性能提升

| 指标 | Phase 0 (Before) | Phase 4 (After) | 提升 |
|------|------------------|-----------------|------|
| Vector store init (cold) | 736.5ms | 0.0ms | 100% |
| Market data (5 peers, cached) | N/A | 0.6ms | ∞ |
| Financial metrics (cached) | N/A | 1.1ms | ∞ |
| Valuation (cached) | N/A | 1.5ms | ∞ |
| Compare peers (warm) | 8,306ms | 3,919ms | 53% |
| 重复 Yahoo 请求 | 2/次 | 0 | 100% |
| 缓存命中率 (market) | 0% | 76.9% | ∞ |

**核心结论：** 缓存 re-run 执行时间 52.9s → 0.8ms（99.98% 降低）

### 19.2 新增文件

**Skill 系统（`app/skills/`）：**
- `base.py` — Skill 基类，遥测 + 重试 + 统一 compute() 接口
- `market_data.py` — Yahoo Finance peer 指标（去重 + 缓存）
- `financial_metrics.py` — 公司财务摘要（数据指纹缓存失效）
- `financial_report_ingestion.py` — SEC filing 下载/解析/索引
- `evidence_retrieval.py` — 向量存储搜索 + 关键词 fallback
- `valuation.py` — 传统估值程序包装器（输出完全一致）

**服务层：**
- `app/services/compute_cache_service.py` — 统一缓存层
- `app/services/performance_tracker.py` — 遥测与指标

**数据库：**
- `app/db/models/execution.py` — NodeExecution 模型
- `alembic/versions/20261017_0006_add_node_executions.py` — 迁移

**测试：**
- `tests/test_skills.py` — 35 个技能测试
- `tests/test_performance_api.py` — 12 个遥测测试

### 19.3 关键 Bug 修复

1. **`skill_span` TypeError** — 缺少 `span_kind` 参数导致 peer-filing 和 market data 路径崩溃
2. **数据指纹静默失败** — `FinancialReport.updated_at` 不存在（只有 `created_at`），导致 metrics 缓存永不失效
3. **`data_availability` 契约破坏** — DataFetcherNode 丢失输出字段，导致 SynthesisReviewer 覆盖率检查失败
4. **LLM token 双重计数** — `record_llm_call` 同时计入 owner span 和 `llm` span
5. **壁钟范围过窄** — 只计算 node span，遗漏 trailing skill span
6. **Ticker 归一化不匹配** — `NVDA` vs `US.NVDA` vs `HK.00700` 查询失败

### 19.4 约束保持

✅ **传统估值程序未修改** — `SupplementalValuationService` 和 `ValuationService` 逻辑不变，包装为 `ValuationSkill` 输出完全一致  
✅ **数据契约保持** — `data_availability` 字段仍在 DataFetcherNode 输出中  
✅ **无破坏性 API 变更** — 所有现有端点仍可用，新端点为增量添加  
✅ **向后兼容** — `force_refresh` 参数可选，默认 `False`

### 19.5 测试覆盖

- **总计：** 301 passed, 1 flaky（timing-sensitive 并行测试在隔离环境通过）
- **新增测试文件：** `test_skills.py` (35), `test_performance_api.py` (12)
- **关键回归测试：**
  - `test_valuation_skill_preserves_legacy_outputs` — 买卖价格完全一致
  - `test_metrics_fingerprint_resolves_against_the_real_schema` — 指纹使用 `created_at`
  - `test_new_financial_data_invalidates_the_metrics_cache` — 缓存失效正常
  - `test_data_fetcher_return_contract_and_state_isolation` — `data_availability` 保持
