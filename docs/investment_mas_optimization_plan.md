# Investment MAS 增量优化方案

> 目标：显著提升研究报告质量，并支持报告生成后的持续问答与用户反馈闭环。
> 原则：增量优化，不重写系统；所有建议可直接指导 Codex / Claude Code 修改代码与验证效果。

---

## 一、问题诊断

### 1.1 当前报告质量不佳的根因分析

| 问题现象 | 根因 | 影响 |
|---------|------|------|
| 商业分析“信息不足” | CompanyProfiler 的 retrieval 只做了 topic 检索 → LLM 一次生成，缺少**证据充分性检查**和**重试/补充检索循环** | 报告该 section 空洞 |
| 主营业务拆分缺失 | 当前 prompt 没有要求按业务板块拆分，`ChunkingService` 也没有做 segment-aware chunking | 集团公司的报告完全扁平，看不出业务结构 |
| 企业文化分析过浅 | `culture_and_governance` 不是 CompanyProfiler 的独立检索维度，没有检索 management/compensation/incentive 相关 chunk | 文化分析变成模板填空 |
| 财务与业务脱节 | `FinancialAnalyst` 和 `CompanyProfiler` 的输出在 `ReportGenerator` 中只是简单拼接，没有做交叉验证 | 报告各部分像是独立文章堆砌 |
| 风险分析模板化 | `RiskAnalyst` 检索 risk_factors chunk 后没有做细粒度证据对齐，只是让 LLM 概括 | 风险清单与年报原文脱节 |
| 无法追问 | 系统没有保存报告上下文和证据引用，没有 followup QA 的 LangGraph 子图 | 一次性报告，无法深化 |

### 1.2 为什么现有节点没有充分利用本地证据

1. **Retrieval 是 "one-shot" 的**：每个 topic 检索一次 → 取 top-k → 扔给 LLM。如果检索到的 chunk 不够好（query 不精确、embedding 不匹配），LLM 就输出“信息不足”。
2. **缺少证据覆盖率检查**：没有在进入 LLM 前判断“检索回来的 chunk 是否真正覆盖了该 topic 的 key questions”。
3. **Chunk 粒度过粗**：当前 chunk_size=1200，一段 chunk 可能混合多个主题，检索精度受损。
4. **没有 query expansion / decomposition**：一个 topic 只有一个 query，没有拆成子问题分别检索。
5. **财务结构化数据和 filing 文本没有联合查询**：财务报表里的 segment revenue 数据和年报里的业务描述 chunk 是两条链路，从未在检索层汇合。

### 1.3 "商业分析信息不足"特别分析

```
用户请求: "分析 NVDA 的商业模式"
当前流程:
  retrieval("business_model") → 10 chunks → LLM
  如果 10 chunks 里有 3 个是 XBRL garbage, 2 个是封面目录, 3 个是风险披露, 只有 2 个真正涉及业务描述
  → LLM 看到的信息确实不足 → 输出“根据现有信息，NVIDIA 主要从事...”
  
问题不在 LLM, 在于:
  (a) retrieval query 太泛
  (b) 没有 query decomposition
  (c) 没有 reranking / filtering
  (d) 没有 evidence sufficiency gate
```

---

## 二、LangGraph 流程优化方案

### 2.1 目标架构概览

```
现有: Supervisor → CompanyProfiler → NewsCollector → FinancialAnalyst → RiskAnalyst → InvestmentExpert → ReportGenerator

优化后 (Phase 2 目标):
Supervisor
  ├── EvidencePlanner (NEW)         ← 生成证据收集计划
  ├── CompanyProfiler (增强)        ← 拆分为子图
  │     ├── SegmentIdentifier (NEW)
  │     ├── BusinessAnalyst (NEW)
  │     ├── CultureAnalyst (NEW)
  │     └── CompetitionAnalyst (NEW)
  ├── NewsCollector (增强)
  ├── FinancialAnalyst (保持)
  ├── RiskAnalyst (增强)
  ├── CrossValidator (NEW)          ← 跨章节交叉验证
  ├── EvidenceCoverageGate (NEW)    ← 证据覆盖率把关
  └── ReportGenerator (增强)

Phase 3 新增 (报告生成后):
  ├── FollowupQARouter (NEW)        ← 用户追问路由
  ├── FollowupQAAgent (NEW)         ← 结合本地证据 + web search 回答
  └── FeedbackRefiner (NEW)         ← 基于用户反馈局部重跑
```

### 2.2 新增节点详细设计

#### 2.2.1 EvidencePlanner（新增）

**职责**：在工作流入口处，根据 `research_request` 生成一份"证据收集计划"，列出每个 report section 需要的证据类型和检索策略。

**输入**：`ticker`, `company_name`, `market`, 可选 `user_focus_areas`
**输出**：`EvidencePlan` dict

```python
# EvidencePlan 结构
{
  "sections": {
    "business_model": {
      "required_evidence_types": ["filing_business_description", "segment_revenue", "product_list"],
      "queries": ["主营业务是什么", "收入来源", "产品线"],
      "priority": "high"
    },
    "culture_and_governance": {
      "required_evidence_types": ["management_bio", "compensation_policy", "equity_incentive", "board_structure"],
      "queries": ["管理层背景", "薪酬政策", "股权激励"],
      "priority": "medium"
    },
    ...
  }
}
```

**实现要点**：
- 使用 LLM 根据 ticker/industry 动态生成 queries（或维护 per-industry 模板）
- 此节点**不检索数据**，只做计划

#### 2.2.2 CompanyProfiler 拆分为子图

将当前单体 `CompanyProfiler` 拆为 `company_profiler_subgraph`，内含：

```
CompanyProfilerSubGraph:
  SegmentIdentifier → [BusinessAnalyst, CultureAnalyst, CompetitionAnalyst] (并行) → ProfilerAggregator
```

| 子节点 | 职责 | 检索策略 |
|--------|------|----------|
| `SegmentIdentifier` | 识别集团公司的业务板块，从年报 segment reporting / 财报 segment_revenue 中提取板块列表 | 优先查 financial_reports 表 segment 字段 + filing chunks 中的 "segment" 相关段落 |
| `BusinessAnalyst` | 商业模式、主营产品、收入模型、市场空间 | 分板块检索 business_model/revenue_model/key_products/key_regions/market_size chunks |
| `CultureAnalyst` | 管理层、企业文化、薪酬与激励 | 检索 management/corporate_culture/compensation/incentive chunks + 公司基本信息中的 management 字段 |
| `CompetitionAnalyst` | 竞争格局、同行对比 | 检索 competition chunks + peer_mappings + 新闻中的竞争动态 |
| `ProfilerAggregator` | 汇总各子节点输出，检查板块覆盖率 | 合并 + 去冲突 |

**关键改进**：
- `SegmentIdentifier` 的输出驱动 `BusinessAnalyst` 和 `CompetitionAnalyst` 按板块分别检索和分析
- 如果 `SegmentIdentifier` 没有识别出多板块（单体公司），则 `BusinessAnalyst` 退化为整体分析
- `CultureAnalyst` 独立出来后，有专门的检索 query set，不再被 CompanyProfiler 的 8 个 topic 平均分配注意力

#### 2.2.3 EvidenceCoverageGate（新增）

**职责**：在进入 ReportGenerator 之前，检查各个 section 的证据是否充分。

**检查逻辑**：
```python
def check_evidence_coverage(state):
    checks = []
    for section in ["business_model", "culture", "competition", "risk"]:
        evidence_count = len(state.evidence[section])
        token_count = sum(len(e) for e in state.evidence[section])
        if evidence_count < MIN_EVIDENCE_CHUNKS_PER_SECTION:  # 默认 5
            checks.append({"section": section, "status": "insufficient", "reason": f"仅{evidence_count}条证据"})
        if token_count < MIN_EVIDENCE_TOKENS_PER_SECTION:  # 默认 2000
            checks.append({"section": section, "status": "insufficient", "reason": f"证据总长度仅{token_count}"})
    
    if any(c["status"] == "insufficient" for c in checks):
        return "retry_with_expanded_queries"  # 回到 EvidencePlanner 扩展 query
    return "proceed_to_report"
```

**策略**：
- 不通过 → 自动触发 query expansion + 重新检索（最多 2 轮）
- 2 轮后仍不通过 → 标记该 section 为 `evidence_limited: true`，允许 LLM 明确声明“该部分证据不足”，而不是硬编

#### 2.2.4 CrossValidator（新增）

**职责**：在 ReportGenerator 之前，检查各 section 之间的一致性。

**输入**：BusinessAnalyst 的 segment_revenue 估算 vs FinancialAnalyst 的实际财务数据
**检查项**：
- 业务板块描述中的收入比例 vs 财务数据中的 segment reporting
- 竞争分析中的市场份额 vs 行业数据
- 风险分析中的风险因素 vs 业务分析中的依赖关系

**输出**：`cross_validation_notes`，标记不一致之处，供 ReportGenerator 参考

#### 2.2.5 FollowupQARouter 和 FollowupQAAgent（Phase 3）

**FollowupQARouter 职责**：分析用户追问意图，路由到正确的回答策略。

```python
# 意图分类
intents = {
    "evidence_lookup": "这个结论依据哪段年报？→ 检索原 chunk 并引用",
    "section_deep_dive": "重点分析 AI 数据中心 → 触发单 section 重检索 + 生成",
    "data_query": "当前 PE 是多少？→ 查 local database",
    "news_update": "结合最新新闻更新 → web search + 增量更新",
    "feedback_refine": "太浅了, 再详细一点 → 触发 FeedbackRefiner",
}
```

**FollowupQAAgent 职责**：执行实际的回答生成，综合利用：
1. 向量库检索（按用户问题语义检索）
2. 结构化数据库查询（SQL 查财务报表、公司信息）
3. Web search（获取最新信息）
4. 已生成的报告作为上下文

#### 2.2.6 FeedbackRefiner（Phase 3）

**职责**：接收用户反馈（如“商业分析太浅”），触发单 section 的深度重跑。

**流程**：
```
用户反馈 → FeedbackRefiner 解析意图
  → 识别目标 section
  → 扩展该 section 的 retrieval query
  → 增加 retrieval top-k
  → 重新生成该 section
  → 合并回完整报告
```

**不会**全量重跑，只重跑受影响的 section。

### 2.3 支持持续追问的 LangGraph 对话流程

在 Phase 3 中，新增一个独立的 followup 子图：

```python
# 新路由: /research/{run_id}/followup
followup_graph = StateGraph(FollowupState)
followup_graph.add_node("intent_router", FollowupQARouter)
followup_graph.add_node("deep_retriever", FollowupRetriever)  # 多源检索
followup_graph.add_node("qa_responder", FollowupQAAgent)      # 生成回答
followup_graph.add_node("feedback_refiner", FeedbackRefiner)  # 局部重跑

# 状态中保留:
# - run_id → session_history → 原始报告 → evidence_map → 用户反馈历史
```

### 2.4 支持用户反馈驱动的局部重跑

```python
# POST /research/{run_id}/refine
{
  "feedback_type": "too_shallow",  # too_shallow | evidence_insufficient | focus_area | redo_section
  "target_section": "business_analysis",  # 或 null 表示整体
  "user_instruction": "重点分析 AI 数据中心业务板块的市场空间和竞争格局",
  "focus_areas": ["AI数据中心", "GPU市场份额"]
}

# 处理流程:
# 1. 解析 target_section
# 2. 构建增强 query（加入 user_instruction + focus_areas）
# 3. 重新检索 + 重新生成该 section
# 4. 合并回原报告, 标记 refined_from_feedback
# 5. 返回更新后的完整报告
```

---

## 三、LLM Prompt 优化方案

### 3.1 核心设计原则

1. **强制证据驱动**：每个判断必须有 `evidence_ref` 引用，禁止凭空输出
2. **板块拆分**：集团公司必须按 segment 维度输出
3. **Output schema 约束**：每个 prompt 都绑定严格的 JSON schema，避免自由发挥
4. **信息不足处理**：允许声明 `evidence_limited: true` 并说明缺少什么信息，不能含糊说"信息不足"
5. **证据→判断→影响**：三段式输出格式

### 3.2 各模块 Prompt 设计

#### 3.2.1 CompanyProfiler → BusinessAnalyst

```
System Prompt 结构:
1. 角色定义：你是资深行业分析师，专注于商业分析
2. 任务说明：基于提供的证据 chunks，分析目标公司的商业模式
3. 约束条件：
   - 仅基于提供的证据，不要引入外部知识
   - 如果证据不足，在对应字段标记 evidence_limited: true，并说明缺少什么
   - 集团公司必须按业务板块拆分
   - 每个判断必须引用证据 ID
4. 输出 Schema (见第六节)

User Prompt 结构:
1. 公司：{company_name} ({ticker})
2. 业务板块：{segments} (来自 SegmentIdentifier)
3. 证据材料：{evidence_chunks} (每个 chunk 带 id, source, topic)
4. 分析要求：
   - 对每个业务板块：描述业务内容、收入占比(如果有)、市场地位、增长驱动
   - 整体商业模式：收入模型、客户群体、关键合作伙伴
   - 市场空间：TAM/SAM/SOM（如果证据支持）
   - 竞争格局：主要竞争对手、竞争优势、护城河
```

**关键 Prompt 技巧**：
- 用 `## Constraints` 段落明确禁止行为
- 用 `## Output Format` 给出 JSON schema 骨架
- 用 `## Evidence Mapping` 要求每个 claim 后标注 `[ref: chunk_id]`

#### 3.2.2 CultureAnalyst

```
System Prompt 结构:
1. 角色定义：你是公司治理和企业文化分析师
2. 分析维度（按顺序）：
   a. 管理层背景：CEO/CFO/核心高管的行业经验、任职年限、过往业绩
   b. 管理层稳定性：过去3-5年核心管理层变动情况
   c. 董事会结构：独立董事占比、委员会设置
   d. 员工薪酬：薪酬策略、与同行的比较
   e. 股权激励：激励计划类型、覆盖范围、行权条件
   f. 企业文化：使命愿景、价值观描述、ESG表现
3. 约束：
   - 每个维度至少引用1条证据
   - 如果某个维度完全没有证据，标记为 evidence_unavailable，不要编造
   - 管理层稳定性评估需要时间序列对比，如果只有单年数据请说明局限性
4. 输出 Schema: 
   {
     "management_info": [...],
     "management_stability_assessment": {...},
     "board_structure": {...},
     "compensation_analysis": {...},
     "equity_incentive_analysis": {...},
     "corporate_culture": {...},
     "evidence_availability": {
       "management_info": "full|partial|unavailable",
       ...
     }
   }
```

#### 3.2.3 RiskAnalyst

改进重点：**从模板化 → 证据驱动**

```
System Prompt 结构:
1. 角色定义：你是风险分析专家
2. 风险分析框架：
   a. 年报披露风险 (从 risk_factors chunks 提取)
      - 对每条风险：原始描述 → 业务影响评估 → 发生概率判断 → 影响程度评级(高/中/低)
   b. 财务风险 (从财务数据 + 历史趋势计算)
      - 负债水平、现金流、应收账款、库存周转
   c. 行业/宏观风险 (从 news + 竞争分析提取)
   d. 治理风险 (从 culture/governance 证据提取)
3. 约束：
   - 每条风险必须引用来源 (filing chunk id / financial metric / news id)
   - 不能只列风险名称, 必须评估具体影响
   - 区分"年报自述风险"与"独立判断的风险"
4. 输出要求：
   - top 10 风险清单, 按影响程度排序
   - 每个风险: 描述 → 证据引用 → 影响评估 → 应对建议
```

#### 3.2.4 ReportGenerator

**最大的改动点**：从"拼接各节点输出"变为"综合分析 + 交叉验证"

```
System Prompt 结构:
1. 角色定义：你是资深投资分析师，正在撰写一份面向投资决策委员会的研究报告
2. 输入材料：
   - {business_analysis} (含 segment 拆分)
   - {culture_analysis}
   - {financial_analysis}
   - {valuation_analysis}
   - {risk_analysis}
   - {cross_validation_notes}
   - {news_events}
3. 报告要求：
   a. 投资摘要 (executive_summary)：100字以内的核心判断
   b. 商业分析：按板块拆解，每个板块有独立的收入/利润/增长/竞争分析
   c. 财务分析：关键指标趋势，与商业分析的交叉验证
   d. 估值分析：提供估值区间，说明关键假设
   e. 风险分析：核心风险清单，与商业分析的关联
   f. 证据索引：报告中每个数据/判断的后面的 [ref:xxx] 对应附录中的证据列表
4. 风格要求：
   - 投资委员会材料风格，简洁有力
   - 每个判断必须：证据 → 分析 → 结论
   - 不确定的地方明确标注
   - 不要重复大段原文，提炼关键信息
5. 输出 Schema: 见第六节

User Prompt 中传入的 cross_validation_notes 格式:
{
  "inconsistencies": [
    {"type": "revenue_mismatch", "business_says": "AI业务占比60%", "financial_shows": "segment reporting 显示 Data Center 占78%", "resolution_suggestion": "核实 AI 业务和 Data Center 的对应关系"}
  ],
  "missing_evidence": [
    {"section": "competition", "detail": "无法确定 AMD 在数据中心 GPU 市场份额"}
  ]
}
```

#### 3.2.5 FollowupQAAgent

```
System Prompt 结构:
1. 角色定义：你是投资研究助手，帮助用户深入理解已生成的研究报告
2. 上下文：
   - 完整报告 (json)
   - 原始证据 map (chunk_id → content)
   - 对话历史
3. 回答策略：
   - 如果用户问"依据哪段年报" → 输出原 chunk 内容 + filing 来源信息
   - 如果用户问"深入某板块" → 从原证据中重新提取相关信息, 更详细展开
   - 如果用户问"最新情况" → 触发 web search, 将新信息与报告合并
   - 如果用户反馈"太浅" → 扩展回答深度
4. 约束：
   - 始终引用具体证据
   - 如果现有证据不足, 明确说明并建议获取途径
```

### 3.3 推荐的 Prompt 结构模板

```yaml
# 通用模板结构
system_prompt:
  role: "明确角色"
  context: "公司/行业特定背景（可选）"
  task: "具体要完成的任务"
  constraints:
    - "仅基于提供的证据"
    - "每个判断必须标注证据引用"
    - "证据不足时明确声明"
    - "集团企业按板块拆分"
  output_schema: "JSON schema 定义"
  quality_checklist:
    - "是否每个 claim 都有引用"
    - "是否覆盖了所有要求的维度"
    - "是否有板块拆分（如适用）"

user_prompt:
  company_info: "{name, ticker, market, industry}"
  segments: "{业务板块列表（如适用）}"
  evidence_materials:
    - id: "chunk_001"
      source: "filing FY2024 page 12"
      topic: "business_model"
      content: "..."
  analysis_requirements: "具体要求"
  special_instructions: "额外要求"
```

---

## 四、本地数据库与检索层优化方案

### 4.1 数据组织优化

#### 4.1.1 现有数据资产盘点

| 数据资产 | 存储位置 | 当前利用率 | 优化方向 |
|---------|---------|-----------|---------|
| filing 原文件 | `data/company_docs/` | 作为 chunk source，但未做结构化标注 | 增加 metadata 索引 |
| filing chunks | `filing_chunks` 表 + ChromaDB | 检索用，但 query 质量不高 | 改进检索策略 |
| 公司基本信息 | `companies` 表 / `company_profiles` 表 | 基础信息展示 | 增加 management/compensation 字段 |
| 三张财务报表 | `financial_reports` 表 | FinancialAnalyst 使用 | 与业务分析做交叉查询 |
| 同行映射 | `peer_mappings` 表 | CompetitionAnalyst 使用 | 增加同行财务对比快照 |
| 新闻/事件 | `news_articles` / `news_events` 表 | NewsCollector 使用 | 增加事件影响评估 |
| 研究报告 | `research_reports` 表 | 一次性存储 | 改为 session 化 |

#### 4.1.2 建议新增的数据表

```
-- 1. 业务板块档案
CREATE TABLE company_segment_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    segment_name VARCHAR(200) NOT NULL,        -- e.g. "Data Center", "Gaming"
    segment_type VARCHAR(50),                   -- product / geography / customer
    revenue_share_pct DECIMAL(5,2),
    profit_share_pct DECIMAL(5,2),
    growth_rate_pct DECIMAL(6,2),
    description TEXT,
    evidence_chunk_ids TEXT[],                  -- 支持此板块分析的 chunk 引用
    extracted_at TIMESTAMPTZ DEFAULT now(),
    extraction_method VARCHAR(50)               -- llm | rule | manual
);
CREATE INDEX idx_segment_profiles_company ON company_segment_profiles(company_id);

-- 2. 管理层档案
CREATE TABLE management_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    person_name VARCHAR(200) NOT NULL,
    title VARCHAR(200),                         -- CEO / CFO / ...
    tenure_years DECIMAL(4,1),                  -- 任职年限
    bio_summary TEXT,                           -- 简要背景
    compensation_summary TEXT,                  -- 薪酬概要
    equity_holdings TEXT,                       -- 持股情况
    evidence_chunk_ids TEXT[],
    extracted_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_management_company ON management_profiles(company_id);

-- 3. 股权激励记录
CREATE TABLE incentive_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    plan_name VARCHAR(200),
    plan_type VARCHAR(50),                      -- stock_option / rsu / espp
    coverage_scope TEXT,                        -- 覆盖范围
    vesting_terms TEXT,                         -- 行权条件
    dilution_pct DECIMAL(5,2),
    evidence_chunk_ids TEXT[],
    extracted_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_incentive_company ON incentive_records(company_id);

-- 4. 证据跨度表（用于精确引用）
CREATE TABLE evidence_spans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filing_chunk_id UUID REFERENCES filing_chunks(id),
    span_type VARCHAR(50),                      -- financial_figure / business_description / risk_factor / ...
    topic_tags TEXT[],                          -- [business_model, data_center, ai]
    segment_name VARCHAR(200),                  -- 关联的业务板块
    start_offset INT,
    end_offset INT,
    content_text TEXT,                          -- span 原文
    summary TEXT,                               -- span 摘要
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_evidence_spans_chunk ON evidence_spans(filing_chunk_id);
CREATE INDEX idx_evidence_spans_tags ON evidence_spans USING GIN(topic_tags);
CREATE INDEX idx_evidence_spans_segment ON evidence_spans(segment_name);

-- 5. 报告会话（支持多轮追问）
CREATE TABLE report_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    research_run_id UUID REFERENCES research_reports(id),
    user_id VARCHAR(200),
    status VARCHAR(50) DEFAULT 'active',        -- active / archived
    full_report_json JSONB,                     -- 完整报告快照
    evidence_map JSONB,                         -- chunk_id → content 映射
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 6. 追问记录
CREATE TABLE followup_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES report_sessions(id),
    question_text TEXT NOT NULL,
    question_intent VARCHAR(50),                -- evidence_lookup / section_deep_dive / data_query / news_update
    target_section VARCHAR(50),
    answer_text TEXT,
    evidence_refs TEXT[],                       -- 引用 chunk id
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_followup_session ON followup_questions(session_id);

-- 7. 用户反馈
CREATE TABLE user_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES report_sessions(id),
    report_section VARCHAR(100),                -- null 表示整体反馈
    feedback_type VARCHAR(50),                  -- useful / not_useful / too_shallow / evidence_insufficient / want_deeper
    user_comment TEXT,
    refinement_triggered BOOLEAN DEFAULT false,
    refinement_result JSONB,                    -- 修正后的 section 内容
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_feedback_session ON user_feedback(session_id);
```

#### 4.1.3 已有表需要增加的字段

```sql
-- company_profiles 表增加
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS segments JSONB;      -- 业务板块列表
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS management_info JSONB; -- 管理层摘要
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS evidence_refs JSONB;  -- 证据引用 map

-- filing_chunks 表增加
ALTER TABLE filing_chunks ADD COLUMN IF NOT EXISTS topic_tags TEXT[];       -- 细粒度主题标签
ALTER TABLE filing_chunks ADD COLUMN IF NOT EXISTS segment_name VARCHAR(200); -- 关联业务板块
ALTER TABLE filing_chunks ADD COLUMN IF NOT EXISTS evidence_spans JSONB;    -- 证据 span 标注

-- research_reports 表增加
ALTER TABLE research_reports ADD COLUMN IF NOT EXISTS session_id UUID;
ALTER TABLE research_reports ADD COLUMN IF NOT EXISTS evidence_map JSONB;
ALTER TABLE research_reports ADD COLUMN IF NOT EXISTS report_version INT DEFAULT 1;  -- 支持多次 refine
```

### 4.2 检索策略优化

#### 4.2.1 分层检索策略 (Retrieval Policy)

```
检索顺序 (由快到慢, 由结构化到非结构化):

Level 1: 结构化数据库查询 (PostgreSQL)
  - 公司基本信息: SELECT FROM companies, company_profiles
  - 财务报表: SELECT FROM financial_reports
  - 管理层档案: SELECT FROM management_profiles
  - 业务板块: SELECT FROM company_segment_profiles
  - 同行数据: SELECT FROM peer_mappings + financial_peer_snapshots

Level 2: 向量检索 (ChromaDB)
  - 按 topic 检索 filing chunks
  - 按 segment 过滤 (metadata filter)
  - Query expansion + multi-query retrieval

Level 3: Web Search (外部)
  - 实时新闻
  - 最新行业报告
  - 补充 market data
```

#### 4.2.2 Query Planning 和 Topic Routing

```python
# EvidencePlanner 生成检索计划后, 由 QueryPlanner 执行
class QueryPlanner:
    def execute_plan(self, evidence_plan: dict) -> EvidenceBundle:
        for section, plan in evidence_plan["sections"].items():
            # Step 1: 结构化查询
            structured_data = self.query_structured(plan["required_evidence_types"])
            
            # Step 2: 向量检索 (query expansion)
            expanded_queries = self.expand_queries(plan["queries"])
            vector_results = self.multi_query_vector_search(
                queries=expanded_queries,
                filters={"topic": {"$in": plan["topics"]}},
                top_k=plan.get("top_k", 20)
            )
            
            # Step 3: 如果证据不足, 触发 web search
            if self.is_insufficient(structured_data, vector_results):
                web_results = self.web_search(plan["fallback_search_terms"])
            
            # Step 4: 合并 + 去重 + 排序
            bundle[section] = self.merge_and_rank(structured_data, vector_results, web_results)
        
        return bundle
    
    def expand_queries(self, base_queries: list[str]) -> list[str]:
        """用 LLM 把一个 query 拆成多个子 query, 提高召回率"""
        # e.g., "商业模式" → ["NVIDIA 主营业务构成", "NVIDIA 收入来源", "NVIDIA 产品线", "NVIDIA 客户群体"]
        
    def multi_query_vector_search(self, queries, filters, top_k):
        """对每个 query 分别检索, 合并结果, 按 relevance score 排序去重"""
```

#### 4.2.3 Reranking 和 Evidence 过滤

```python
class EvidenceReranker:
    def rerank(self, chunks: list[Chunk], query: str) -> list[Chunk]:
        """
        使用 cross-encoder 或 LLM 对检索结果重排序
        如果没有 cross-encoder, 用 LLM 做 pairwise comparison
        """
    
    def filter_by_relevance_threshold(self, chunks: list[Chunk], threshold: float) -> list[Chunk]:
        """过滤低相关度 chunk, 避免 LLM 被噪音干扰"""
    
    def deduplicate_by_content(self, chunks: list[Chunk]) -> list[Chunk]:
        """基于文本相似度去重 (临近 chunk 经常高度重叠)"""
```

### 4.3 向量存储和结构化数据库的联合检索

```python
class HybridRetriever:
    """联合检索器 - 同时查询 PG 和 Chroma, 合并结果"""
    
    def retrieve_for_section(self, section: str, query: RetrievalQuery) -> EvidenceBundle:
        results = EvidenceBundle()
        
        # 并行查询
        with ThreadPoolExecutor() as executor:
            pg_future = executor.submit(self._query_structured_db, query.structured_queries)
            vector_future = executor.submit(self._query_vector_store, query.vector_queries)
        
        results.structured = pg_future.result()
        results.chunks = vector_future.result()
        
        # 关联 chunk 与结构化数据
        # e.g., chunk 提到 "Data Center revenue grew 112%" → 关联到 financial_reports 中的 segment_revenue 字段
        results.cross_references = self._link_structured_and_chunks(results)
        
        return results
```

---

## 五、用户界面（UI/UX）优化方案

### 5.1 整体布局设计

```
┌──────────────────────────────────────────────────────┐
│  Investment MAS - {Company Name} ({Ticker})           │
│  [新建研究] [历史报告] [设置]          [模型选择 ▾]    │
├──────────┬───────────────────────────┬────────────────┤
│          │                           │                │
│  导航    │     报告主区域             │   证据侧栏     │
│  面板    │                           │   (可收起)     │
│          │                           │                │
│  ▸ 投资  │  ## 投资摘要             │  📎 相关证据   │
│    摘要  │  NVIDIA 是全球领先的...   │  ┌──────────┐  │
│          │                           │  │ chunk_42 │  │
│  ▸ 商业  │  ## 商业分析             │  │ FY2025   │  │
│    分析  │  ### Data Center (78%)   │  │ page 12  │  │
│  ▸ 企业  │  数据中心业务是...       │  │ "...NVI- │  │
│    文化  │                           │  │ DIA's    │  │
│  ▸ 财务  │  ### Gaming (22%)       │  │ Data     │  │
│    分析  │  游戏业务...             │  │ Center   │  │
│  ▸ 估值  │                           │  │ revenue  │  │
│    分析  │  ## 竞争格局             │  │ grew..." │  │
│  ▸ 风险  │  ...                     │  └──────────┘  │
│    分析  │                           │                │
│          │                           │  📊 财务数据   │
│          │                           │  PE: 45.2     │
│          │                           │  ROE: 32.1%   │
│          │                           │                │
├──────────┴───────────────────────────┴────────────────┤
│  💬 追问与反馈                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │ 用户: Data Center 业务的竞争对手有哪些？        │  │
│  │ AI: 根据年报 FY2025 p.45 [chunk_128]...         │  │
│  │     主要竞争对手包括 AMD (MI300X)...             │  │
│  │                                                 │  │
│  │ 用户: 深入分析 AI 芯片市场份额                   │  │
│  │ AI: 正在重新检索相关证据... [展开详细分析]       │  │
│  └─────────────────────────────────────────────────┘  │
│  [输入框___________________________________] [发送]   │
│  [有用👍] [无用👎] [证据不足] [太空泛] [更多...▾]      │
└──────────────────────────────────────────────────────┘
```

### 5.2 各区域详细设计

#### 5.2.1 研究输入页

```
组件: ResearchInputPanel
- 公司选择器 (搜索 ticker/名称, 支持下拉建议)
- 研究范围选项:
  [x] 商业模式  [x] 市场空间  [x] 竞争格局
  [x] 企业文化  [x] 财务分析  [x] 估值分析
  [x] 风险评估
- 深度选项: [标准] [深度] [快速]
- 对比公司 (可选): [输入 ticker, 多选]
- 特殊关注点 (可选): [自由文本]
- [开始研究] 按钮
```

#### 5.2.2 报告页 (核心)

```
组件: ReportViewer
- 顶部: 进度条 (显示工作流各节点状态)
- 左侧: Section 导航 (点击滚动到对应 section)
- 中间: Markdown 渲染的报告内容
  - 每个关键 claim 内嵌引用标记, 例如: "NVIDIA 数据中心业务营收同比增长 112% [ref:chunk_42, FY2025_p12]"
  - 点击引用标记展开证据原文
  - 财务指标旁有迷你图表 (sparkline)
  - 风险清单以优先级排序的卡片展示
  - 估值区间以范围条 (range bar) 展示
- 右侧: 证据侧栏 (EvidenceSidebar)
  - 显示当前 section 关联的所有证据
  - 每条证据: chunk 摘要 + 来源 + 高亮相关段落
  - 支持筛选: [财务数据] [年报引用] [新闻] [行业数据]
```

#### 5.2.3 问答区 (Phase 3)

```
组件: FollowupChat
- 位置: 报告页底部, 可展开/收起
- 功能:
  - 建议追问: AI 根据报告自动生成 3-5 个建议追问
  - 自由输入: 用户可以输入任何问题
  - 答案格式: Markdown + 证据引用
  - 答案中每个引用都是可点击的, 点击后右侧证据侧栏高亮对应 chunk
  - 支持"重新生成此 section" (右键菜单或按钮)
- 上下文: 保留报告 + evidence_map 作为 LLM 上下文
- 记忆: conversation history 存储到 followup_questions 表
```

#### 5.2.4 反馈区

```
组件: FeedbackBar
- 每个 section 底部有快速反馈按钮:
  [👍 有用] [👎 无用] [📄 证据不足] [🌫️ 太空泛] [⬇️ 希望更深入]
- 整体报告底部有反馈:
  [整体评分: ⭐⭐⭐⭐☆]
  [反馈输入框] [提交反馈]
- 反馈触发:
  - "证据不足" → 自动触发该 section 的补充检索
  - "太空泛" → 触发深度增强 (增加 detail_level)
  - "希望更深入" → 弹出输入框指定关注点, 触发 FeedbackRefiner
```

#### 5.2.5 单 Section 重跑

```
组件: SectionActions (在每个 section 标题旁)
- [🔄 重新分析此部分] 按钮
- 点击后弹出选项:
  - 分析深度: [标准] [深入] [极深入]
  - 关注点 (可选): [自由文本]
  - [开始重跑]
- 重跑时显示加载动画, 完成后平滑替换该 section 内容
- 后端: POST /research/{run_id}/refine-section, 只重跑目标 section
```

### 5.3 前端技术实现建议

鉴于项目已有 `app/web/index.html`，建议：

- **Phase 1-2**: 增强现有 HTML，使用原生 JS + 模板字符串，不引入框架
- **Phase 3-4**: 视复杂度决定是否迁移到 React/Vue 或保持轻量

关键前端模块：
```javascript
// 推荐模块结构
- EvidenceHighlighter    // 报告中引用标记高亮 + 点击展开
- EvidenceSidebar        // 证据侧栏
- ReportSectionRenderer  // section 渲染器 (支持板块拆分展示)
- FollowupChatWidget     // 追问对话组件
- FeedbackBar            // 反馈组件
- FinanceSparklines      // 财务指标迷你图
- ValuationRangeBar      // 估值区间图
```

---

## 六、输出结果与 JSON Schema 优化方案

### 6.1 新版研究报告 Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "InvestmentResearchReport",
  "type": "object",
  "required": [
    "report_metadata",
    "executive_summary",
    "business_model_analysis",
    "market_space_analysis",
    "competition_analysis",
    "culture_and_governance_analysis",
    "financial_analysis",
    "valuation_analysis",
    "risk_analysis",
    "final_recommendation",
    "evidence_index",
    "followup_suggestions"
  ],
  "properties": {
    "report_metadata": {
      "type": "object",
      "properties": {
        "report_id": {"type": "string"},
        "company_name": {"type": "string"},
        "ticker": {"type": "string"},
        "market": {"type": "string"},
        "generated_at": {"type": "string", "format": "date-time"},
        "data_as_of_date": {"type": "string", "format": "date"},
        "evidence_coverage_summary": {
          "type": "object",
          "description": "每个 section 的证据覆盖情况",
          "additionalProperties": {
            "type": "object",
            "properties": {
              "status": {"enum": ["sufficient", "partial", "insufficient"]},
              "evidence_count": {"type": "integer"},
              "primary_sources": {"type": "array", "items": {"type": "string"}}
            }
          }
        }
      }
    },

    "executive_summary": {
      "type": "object",
      "properties": {
        "one_liner": {"type": "string", "description": "一句话投资判断"},
        "key_positives": {"type": "array", "items": {"$ref": "#/definitions/ClaimWithEvidence"}},
        "key_risks": {"type": "array", "items": {"$ref": "#/definitions/ClaimWithEvidence"}},
        "valuation_stance": {"type": "string", "enum": ["undervalued", "fairly_valued", "overvalued", "uncertain"]},
        "recommendation_summary": {"type": "string"}
      }
    },

    "business_model_analysis": {
      "type": "object",
      "properties": {
        "overview": {"type": "string"},
        "segments": {
          "type": "array",
          "description": "业务板块分析 (集团公司必须拆分)",
          "items": {
            "type": "object",
            "required": ["segment_name", "description", "revenue_contribution"],
            "properties": {
              "segment_name": {"type": "string"},
              "revenue_contribution": {
                "type": "object",
                "properties": {
                  "amount": {"type": "number"},
                  "currency": {"type": "string"},
                  "pct_of_total": {"type": "number"},
                  "fiscal_year": {"type": "integer"},
                  "yoy_growth": {"type": "number"}
                }
              },
              "profit_contribution": {"type": "object"},
              "description": {"type": "string"},
              "key_products": {"type": "array", "items": {"type": "string"}},
              "key_customers": {"type": "array", "items": {"type": "string"}},
              "competitive_position": {"type": "string"},
              "growth_drivers": {"type": "array", "items": {"type": "string"}},
              "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}},
              "evidence_limited": {"type": "boolean", "default": false},
              "missing_information": {"type": "string"}
            }
          }
        },
        "revenue_model": {"$ref": "#/definitions/ClaimWithEvidence"},
        "customer_analysis": {"$ref": "#/definitions/ClaimWithEvidence"},
        "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}}
      }
    },

    "market_space_analysis": {
      "type": "object",
      "properties": {
        "overall_tam": {"$ref": "#/definitions/ClaimWithEvidence"},
        "by_segment": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "segment_name": {"type": "string"},
              "tam_estimate": {"type": "number"},
              "tam_unit": {"type": "string"},
              "growth_rate": {"type": "number"},
              "key_trends": {"type": "array", "items": {"type": "string"}},
              "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
            }
          }
        },
        "open_questions": {"type": "array", "items": {"type": "string"}}
      }
    },

    "competition_analysis": {
      "type": "object",
      "properties": {
        "overall_assessment": {"type": "string"},
        "by_segment": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "segment_name": {"type": "string"},
              "market_share_estimate": {"type": "string"},
              "key_competitors": {
                "type": "array",
                "items": {
                  "type": "object",
                  "properties": {
                    "name": {"type": "string"},
                    "ticker": {"type": "string"},
                    "competitive_advantage": {"type": "string"},
                    "threat_level": {"enum": ["high", "medium", "low"]}
                  }
                }
              },
              "moat_assessment": {"$ref": "#/definitions/ClaimWithEvidence"},
              "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
            }
          }
        },
        "peer_comparison_table": {
          "type": "array",
          "description": "关键指标同行对比",
          "items": {
            "type": "object",
            "properties": {
              "ticker": {"type": "string"},
              "revenue_growth": {"type": "number"},
              "gross_margin": {"type": "number"},
              "net_margin": {"type": "number"},
              "roe": {"type": "number"},
              "pe_ratio": {"type": "number"}
            }
          }
        },
        "open_questions": {"type": "array", "items": {"type": "string"}}
      }
    },

    "culture_and_governance_analysis": {
      "type": "object",
      "properties": {
        "management_team": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "name": {"type": "string"},
              "title": {"type": "string"},
              "tenure_years": {"type": "number"},
              "background_summary": {"type": "string"},
              "track_record": {"type": "string"},
              "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
            }
          }
        },
        "management_stability": {
          "type": "object",
          "properties": {
            "assessment": {"type": "string", "enum": ["highly_stable", "stable", "some_turnover", "high_turnover", "unknown"]},
            "key_changes_3y": {"type": "array", "items": {"type": "string"}},
            "succession_plan": {"type": "string"},
            "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}},
            "evidence_limited": {"type": "boolean"}
          }
        },
        "board_structure": {"$ref": "#/definitions/ClaimWithEvidence"},
        "compensation": {
          "type": "object",
          "properties": {
            "philosophy": {"type": "string"},
            "ceo_pay_ratio": {"type": "string"},
            "employee_compensation": {"type": "string"},
            "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
          }
        },
        "equity_incentives": {
          "type": "object",
          "properties": {
            "active_plans": {"type": "array", "items": {"type": "string"}},
            "dilution_risk": {"type": "string"},
            "vesting_terms": {"type": "string"},
            "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
          }
        },
        "corporate_culture": {"$ref": "#/definitions/ClaimWithEvidence"},
        "esg_highlights": {"$ref": "#/definitions/ClaimWithEvidence"},
        "evidence_availability": {
          "type": "object",
          "properties": {
            "management_info": {"enum": ["full", "partial", "unavailable"]},
            "stability_data": {"enum": ["full", "partial", "unavailable"]},
            "compensation_data": {"enum": ["full", "partial", "unavailable"]},
            "incentive_data": {"enum": ["full", "partial", "unavailable"]},
            "culture_data": {"enum": ["full", "partial", "unavailable"]}
          }
        },
        "open_questions": {"type": "array", "items": {"type": "string"}}
      }
    },

    "financial_analysis": {
      "type": "object",
      "properties": {
        "key_metrics_trend": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "metric_name": {"type": "string"},
              "fiscal_years": {
                "type": "object",
                "additionalProperties": {"type": "number"}
              },
              "trend_direction": {"enum": ["improving", "stable", "declining", "volatile"]},
              "assessment": {"type": "string"}
            }
          }
        },
        "profitability_analysis": {"$ref": "#/definitions/ClaimWithEvidence"},
        "balance_sheet_health": {"$ref": "#/definitions/ClaimWithEvidence"},
        "cash_flow_analysis": {"$ref": "#/definitions/ClaimWithEvidence"},
        "segment_financials": {
          "type": "array",
          "description": "各板块财务表现 (如可用)",
          "items": {
            "type": "object",
            "properties": {
              "segment_name": {"type": "string"},
              "revenue": {"type": "number"},
              "revenue_growth": {"type": "number"},
              "operating_income": {"type": "number"},
              "margin": {"type": "number"}
            }
          }
        },
        "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
      }
    },

    "valuation_analysis": {
      "type": "object",
      "properties": {
        "current_price": {"type": "number"},
        "valuation_methods": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "method": {"type": "string", "enum": ["pe_relative", "pb_relative", "dcf", "ddm", "ev_ebitda", "value_investment"]},
              "fair_value_range": {
                "type": "object",
                "properties": {
                  "low": {"type": "number"},
                  "high": {"type": "number"},
                  "unit": {"type": "string"}
                }
              },
              "key_assumptions": {"type": "array", "items": {"type": "string"}},
              "upside_downside": {"type": "number", "description": "正数为上涨空间, 负数为下跌空间"},
              "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
            }
          }
        },
        "buy_sell_zones": {
          "type": "object",
          "properties": {
            "buy_zone": {"type": "number"},
            "sell_zone": {"type": "number"},
            "methodology": {"type": "string"}
          }
        },
        "sensitivity_analysis": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}}
      }
    },

    "risk_analysis": {
      "type": "object",
      "properties": {
        "top_risks": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "risk_name": {"type": "string"},
              "risk_category": {"enum": ["business", "financial", "regulatory", "competitive", "macro", "governance", "technology"]},
              "description": {"type": "string"},
              "impact_assessment": {"type": "string", "enum": ["high", "medium", "low"]},
              "probability_assessment": {"type": "string", "enum": ["high", "medium", "low"]},
              "source": {"enum": ["filing_disclosed", "independent_analysis", "news_event"]},
              "related_business_segment": {"type": "string"},
              "mitigation_factors": {"type": "string"},
              "supporting_evidence_refs": {"type": "array", "items": {"type": "string"}}
            }
          }
        },
        "risk_matrix_summary": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}}
      }
    },

    "final_recommendation": {
      "type": "object",
      "properties": {
        "recommendation": {"enum": ["strong_buy", "buy", "hold", "sell", "strong_sell"]},
        "target_price_range": {
          "type": "object",
          "properties": {
            "low": {"type": "number"},
            "high": {"type": "number"}
          }
        },
        "investment_thesis": {"$ref": "#/definitions/ClaimWithEvidence"},
        "key_catalysts": {"type": "array", "items": {"type": "string"}},
        "key_risks_to_thesis": {"type": "array", "items": {"type": "string"}},
        "time_horizon": {"type": "string"},
        "confidence_level": {"enum": ["high", "medium", "low"]},
        "confidence_rationale": {"type": "string"}
      }
    },

    "evidence_index": {
      "type": "object",
      "description": "报告中所有引用的证据索引",
      "additionalProperties": {
        "type": "object",
        "properties": {
          "chunk_id": {"type": "string"},
          "source_type": {"enum": ["filing_chunk", "financial_record", "news_article", "company_profile", "peer_data"]},
          "source_description": {"type": "string"},
          "content_excerpt": {"type": "string"},
          "filing_info": {
            "type": "object",
            "properties": {
              "ticker": {"type": "string"},
              "fiscal_year": {"type": "integer"},
              "filing_type": {"type": "string"},
              "section_hint": {"type": "string"}
            }
          }
        }
      }
    },

    "followup_suggestions": {
      "type": "array",
      "description": "AI 建议用户追问的问题",
      "items": {
        "type": "object",
        "properties": {
          "question": {"type": "string"},
          "category": {"enum": ["evidence_lookup", "section_deep_dive", "data_query", "comparison", "scenario"]},
          "target_section": {"type": "string"}
        }
      }
    }
  },

  "definitions": {
    "ClaimWithEvidence": {
      "type": "object",
      "properties": {
        "claim": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "confidence": {"enum": ["high", "medium", "low"]},
        "evidence_limited": {"type": "boolean", "default": false},
        "limitation_note": {"type": "string"}
      }
    }
  }
}
```

---

## 七、增量实施方案

### 7.1 Phase 1：最小可行增强（1-2 周）

**目标**：解决"信息不足"问题，让基本商业分析可用

| 序号 | 改动内容 | 涉及模块 | 验证方式 |
|------|---------|---------|---------|
| P1.1 | 新增 `EvidencePlanner` 节点，生成检索计划 | `graph/nodes/evidence_planner.py` | 检查生成的 plan 是否包含 segment 拆分和 query 扩展 |
| P1.2 | 增强 `CompanyProfiler` 的检索：query expansion + reranking + evidence 过滤 | `services/retrieval_service.py`, `services/company_profile_rag_service.py` | 对比优化前后商业分析 section 的"信息不足"出现率 |
| P1.3 | 在 `CompanyProfiler` prompt 中增加 segment 拆分要求和证据引用约束 | `services/llm_prompt_templates.py` | 集团公司报告是否有板块拆分 |
| P1.4 | 新增 `EvidenceCoverageGate` 节点，至少检查 business_model 和 competition 证据量 | `graph/nodes/evidence_coverage_gate.py` | 进入 ReportGenerator 前是否校验证据覆盖率 |
| P1.5 | 优化 `RiskAnalyst` prompt，要求逐条引用 risk_factors 原文 | `services/llm_prompt_templates.py` | 风险分析中的每条风险是否有 chunk 引用 |
| P1.6 | 新增 `company_segment_profiles` 和 `management_profiles` 表 | `alembic/versions/`, `app/db/models/` | 表结构正确，写入测试通过 |

### 7.2 Phase 2：报告质量增强（2-3 周）

**目标**：让研究报告达到投资委员会材料水平

| 序号 | 改动内容 | 涉及模块 | 验证方式 |
|------|---------|---------|---------|
| P2.1 | 拆分 `CompanyProfiler` 为子图：`SegmentIdentifier → BusinessAnalyst + CultureAnalyst + CompetitionAnalyst → ProfilerAggregator` | `graph/nodes/company_profiler_subgraph/` | 子图独立运行正常，输出完整 |
| P2.2 | 实现 `CultureAnalyst`，覆盖管理层/薪酬/股权激励检索与分析 | `graph/nodes/company_profiler_subgraph/culture_analyst.py` | 文化分析 section 质量评估 |
| P2.3 | 新增 `CrossValidator` 节点，交叉验证业务分析和财务数据 | `graph/nodes/cross_validator.py` | 报告中是否有交叉验证标记 |
| P2.4 | 更新 `ReportGenerator` prompt 和 JSON schema（使用第六节的新 schema） | `services/llm_prompt_templates.py`, `app/schemas/report.py` | 报告是否按新 schema 输出 |
| P2.5 | 新增 `evidence_spans`, `incentive_records` 表 | `alembic/versions/`, `app/db/models/` | 表结构正确 |
| P2.6 | 实现 `HybridRetriever` 联合检索（PG + Chroma） | `services/hybrid_retriever.py` | 检索结果是否同时包含结构化数据和 chunk |

### 7.3 Phase 3：多轮问答与反馈闭环（2-3 周）

**目标**：支持报告生成后的持续追问和用户反馈驱动增强

| 序号 | 改动内容 | 涉及模块 | 验证方式 |
|------|---------|---------|---------|
| P3.1 | 新增 `report_sessions`, `followup_questions`, `user_feedback` 表 | `alembic/versions/`, `app/db/models/` | 表结构正确 |
| P3.2 | 新增 API 路由 `POST /research/{run_id}/followup` 和 `POST /research/{run_id}/refine` | `app/api/routes/research.py` | API 请求响应正确 |
| P3.3 | 实现 `FollowupQAAgent`，整合向量检索 + 结构化查询 + web search | `graph/nodes/followup_qa_agent.py` | 追问回答是否引用证据 |
| P3.4 | 实现 `FeedbackRefiner`，支持局部 section 重跑 | `graph/nodes/feedback_refiner.py` | 单 section 重跑是否成功 |
| P3.5 | 新增独立 followup 子图 | `graph/followup_workflow.py` | 子图编译和运行正常 |
| P3.6 | 在报告中加入 `followup_suggestions` 字段 | `services/llm_prompt_templates.py` | 报告中是否有建议追问 |

### 7.4 Phase 4：界面与生产化优化（2-3 周）

**目标**：让投研 MAS 成为可用产品

| 序号 | 改动内容 | 涉及模块 | 验证方式 |
|------|---------|---------|---------|
| P4.1 | 重写前端 `index.html`：报告页、证据侧栏、导航面板 | `app/web/` | UI 功能完整性检查 |
| P4.2 | 实现 `FollowupChat` 前端组件 | `app/web/` | 追问功能可用 |
| P4.3 | 实现 `FeedbackBar` 前端组件 + 后端存储 | `app/web/`, `app/api/routes/` | 反馈提交和查询正常 |
| P4.4 | 实现单 section 重跑的前端交互 | `app/web/` | 点击"重新分析"后该 section 更新 |
| P4.5 | 报告导出（PDF/Markdown） | `app/services/export_service.py` | 导出文件完整 |
| P4.6 | 历史报告管理页面 | `app/web/`, `app/api/routes/` | 历史列表和查看正常 |

---

## 八、验收标准

| # | 验收标准 | 测量方式 | 目标值 |
|---|---------|---------|--------|
| AC1 | 商业分析不再轻易输出"信息不足" | 统计 10+ 次不同公司的研究报告，商业分析 section 中出现"信息不足"的比例 | < 20%（仅在确实无证时出现） |
| AC2 | 集团公能按板块拆分业务分析 | 对 NVDA/TSM/SONY 等集团公司，报告中 segments 数组不为空 | 100% |
| AC3 | 企业文化部分覆盖管理层和激励信息 | culture_and_governance_analysis 中 management_stability 和 equity_incentives 不为空 | 100%（若本地文档支持） |
| AC4 | 每个主要判断可追溯证据 | 报告中 > 80% 的 ClaimWithEvidence 包含有效的 evidence_refs | > 80% |
| AC5 | 报告生成后用户可以继续提问 | followup API 返回有效回答且引用证据 | 功能可用 |
| AC6 | 用户反馈驱动局部增强 | 提交"too_shallow"反馈后，目标 section 重新生成内容更详细 | 内容长度/引用数量增加 > 30% |
| AC7 | 风险分析逐条引用原文 | risk_analysis.top_risks 中 > 60% 的条目有 supporting_evidence_refs | > 60% |
| AC8 | 工作流在证据不足时不会卡死 | EvidenceCoverageGate 在 2 轮重试后标记 evidence_limited，继续执行 | 100% |

---

## 九、推荐清单汇总

### 9.1 推荐的目标 LangGraph 新流程

```
Supervisor
├── EvidencePlanner                    ← NEW Phase 1
├── FilingCoverageChecker              ← NEW Phase 1 (检查是否需要下载新 filing)
├── CompanyProfilerSubGraph            ← REFACTOR Phase 2
│   ├── SegmentIdentifier              ← NEW
│   ├── BusinessAnalyst                ← NEW (原 CompanyProfiler 拆分)
│   ├── CultureAnalyst                 ← NEW
│   └── CompetitionAnalyst            ← NEW
├── NewsCollector                      ← ENHANCED
├── FinancialAnalyst                   ← KEEP
├── RiskAnalyst                        ← ENHANCED Phase 1
├── CrossValidator                     ← NEW Phase 2
├── EvidenceCoverageGate               ← NEW Phase 1
└── ReportGenerator                    ← ENHANCED Phase 2

报告生成后 (Phase 3):
FollowupQARouter → FollowupQAAgent → FeedbackRefiner (loop)
```

### 9.2 推荐新增的数据表

```sql
-- Phase 1
company_segment_profiles     -- 业务板块档案
management_profiles          -- 管理层档案

-- Phase 2
incentive_records            -- 股权激励记录
evidence_spans               -- 证据粒度标注

-- Phase 3
report_sessions              -- 报告会话
followup_questions           -- 追问记录
user_feedback                -- 用户反馈
```

### 9.3 推荐新增的 UI 组件

1. **ReportSectionRenderer** - 板块拆分可视化
2. **EvidenceSidebar** - 证据侧栏（点击引用标记联动）
3. **EvidenceHighlighter** - 报告中引用标记高亮
4. **FollowupChat** - 追问对话区
5. **FeedbackBar** - 反馈按钮与输入
6. **SectionActions** - 单 section 重跑操作
7. **FinanceSparklines** - 财务指标迷你趋势图
8. **ValuationRangeBar** - 估值区间可视化
9. **RiskCardList** - 风险清单卡片（按影响排序）
10. **SegmentPieChart** - 业务板块收入占比图

### 9.4 推荐优先修改的 Prompt 列表

| 优先级 | Prompt 模块 | 改动内容 |
|-------|-----------|---------|
| P0 | `BusinessAnalyst` | 新写：按板块拆分、证据→判断→影响三段式、证据引用约束 |
| P0 | `CultureAnalyst` | 新写：管理层/稳定性/薪酬/股权激励/企业文化 5 维度 |
| P0 | `RiskAnalyst` | 增强：逐条引用 risk_factors 原文、影响评估、概率判断 |
| P1 | `ReportGenerator` | 增强：交叉验证指令、新 JSON schema 绑定、投资委员会风格 |
| P1 | `EvidencePlanner` | 新写：生成检索计划的 meta-prompt |
| P1 | `CompetitionAnalyst` | 新写：分板块竞争分析、市场份额、护城河评估 |
| P2 | `FollowupQAAgent` | 新写：证据追溯 + 多意图路由回答 |
| P2 | `FeedbackRefiner` | 新写：用户意图解析 + 单 section 深度增强 |

### 9.5 最值得优先落地的 10 项改动

| # | 改动 | Phase | 预期收益 | 复杂度 |
|---|------|-------|---------|--------|
| 1 | **新增 EvidencePlanner + query expansion** | P1 | 解决"信息不足"根因，检索召回率提升 50%+ | 中 |
| 2 | **BusinessAnalyst 拆分 + 板块拆分 prompt** | P2 | 集团公司报告从扁平变立体 | 中 |
| 3 | **CultureAnalyst 独立 + 5 维度 prompt** | P2 | 企业文化从 0 分变 60 分 | 中 |
| 4 | **EvidenceCoverageGate** | P1 | 防止证据不足进入报告生成 | 低 |
| 5 | **RiskAnalyst prompt 增强（逐条引用）** | P1 | 风险分析从模板化变证据驱动 | 低 |
| 6 | **新增 company_segment_profiles 表** | P1 | 业务板块可持久化、可复用 | 低 |
| 7 | **ReportGenerator 新 schema + 交叉验证** | P2 | 报告从拼接变综合分析 | 高 |
| 8 | **报告页前端 + 证据侧栏** | P4 | 用户可查看证据、可信度大幅提升 | 高 |
| 9 | **FollowupQAAgent + API** | P3 | 从一次性报告变持续投研助手 | 高 |
| 10 | **FeedbackRefiner + 局部重跑** | P3 | 闭环反馈机制，持续优化 | 中 |

---

*文档版本：v1.0*
*生成日期：2026-04-26*
*适用范围：Investment MAS 增量优化*