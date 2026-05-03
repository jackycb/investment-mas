# Investment Research MAS 系统架构文档（面向后续 AI Coding）

## 1. 文档目的

本文档用于作为后续 **Codex / Claude Code / DeepSeek v4 Pro** 的统一设计依据，目标是：

1. 明确系统边界与目标能力，避免 AI Coding 跑偏；
2. 统一工作流、数据库、Agent、LLM 调用、检索层、UI 与交互闭环设计；
3. 基于当前已打通的 MAS 系统做**增量优化**，而不是推倒重写；
4. 为后续开发、验证、回归测试、部署和多轮问答提供清晰蓝图。

---

## 2. 现状与问题定义

### 2.1 当前已有能力（基线）
当前系统已经具备以下能力：

- 基于 LangGraph 的串行研究工作流
- 财报优先从本地目录获取，缺失时可回退数据库
- 新闻与公开信息支持抓取
- CompanyProfiler 已接入 filing 文档知识层
- filing 支持：
  - discovery
  - download
  - local storage
  - parsing
  - chunking
  - embedding
  - local vector store
  - retrieval
- CompanyProfiler 已可按 topic 检索：
  - business_model
  - revenue_model
  - key_products
  - key_regions
  - competition
  - management
  - corporate_culture
  - risk_factors
- RiskAnalyst 已可结合 filing chunk 做辅助证据检索
- 本地保存了：
  - 公司年报原文件
  - 向量数据库
  - 公司基本信息（股价、总市值、总股本、股息率）
  - 三张财务报表
- 已有：
  - 年报下载方法
  - 财务报表和基本信息下载方法
  - 财务分析指标方法（PE/PB/ROE/ROIC/Gross Margin/Net Margin）
  - 价值投资估值方法（买入点/卖出点）

### 2.2 当前主要问题
虽然系统主流程已经打通，但最终研究报告质量仍不理想，典型问题包括：

1. **商业分析证据覆盖不够**
   - CompanyProfiler 虽已具备文档检索能力，但没有形成“证据覆盖率检查”和“专题强制补全”机制；
   - 输出仍容易出现“信息不足”。

2. **公司业务拆解不够细**
   - 对集团公司缺少分业务板块分析；
   - 没有系统输出主营业务占比、各业务板块市场情况、竞争情况。

3. **企业文化分析过浅**
   - 管理层信息、管理层稳定性、薪酬、股权激励分析不系统；
   - 没形成独立 Culture/Governance 视角。

4. **财务、估值、业务三者未闭环**
   - 财务分析结果没有足够反哺商业模式和竞争格局判断；
   - 估值分析与业务/风险的联动解释不足。

5. **风险评估偏弱**
   - 主要风险没有充分基于年报披露的 risk factors；
   - 缺少风险证据链与监控指标体系。

6. **系统像“一次性报告生成器”，不像“持续投研助手”**
   - 报告生成后，用户无法持续提问并基于本地知识层获得更深分析；
   - 用户反馈无法驱动局部增强和 section 级重跑。

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
        +--> Research Graph
        +--> Follow-up QA Graph
        +--> Feedback Refinement Graph
        |
        v
[Agent / Node Layer]
        |
        +--> FilingIngestion / FilingCoverageChecker
        +--> CompanyProfiler / BusinessAnalyst / CultureAnalyst / PeerAnalyzer
        +--> NewsCollector
        +--> FinancialAnalyst
        +--> ValuationAgent
        +--> RiskAnalyst
        +--> InvestmentExpert / ReportGenerator
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
        +--> financial_service
        +--> valuation_service
        +--> risk_service
        +--> report_service
        |
        v
[Provider Layer]
        |
        +--> FilingDiscoveryProvider
        +--> FilingDownloadProvider
        +--> VectorStoreProvider
        +--> StructuredFinancialProvider
        +--> NewsProvider
        +--> CompanyInfoProvider
        +--> WebSearchProvider
        +--> LLMProvider
        |
        v
[Persistence Layer]
        |
        +--> PostgreSQL
        +--> Local Document Store
        +--> Local Vector Store
```

---

## 5. 目标工作流设计

### 5.1 研究报告主流程（建议目标版）

当前主流程为：
`CompanyProfiler -> NewsCollector -> FinancialAnalyst -> ValuationAgent -> RiskAnalyst -> InvestmentExpert`

建议升级为：

```text
START
-> FilingIngestion
-> FilingCoverageChecker
-> CompanyProfiler
-> BusinessAnalyst
-> CultureAnalyst
-> PeerAnalyzer
-> NewsCollector
-> FinancialAnalyst
-> ValuationAgent
-> RiskAnalyst
-> ReportPlanner
-> ReportGenerator
-> ReportCoverageChecker
-> END
```

### 说明

#### 1) FilingIngestion
职责：
- 检查本地是否已有目标公司 filing
- 若缺失，触发 filing discovery / download / parse / chunk / vector index
- 若已有，则检查是否需要增量更新

#### 2) FilingCoverageChecker
职责：
- 检查关键专题是否有足够 evidence：
  - business_model
  - competition
  - management
  - compensation
  - risk_factors
  - segments
- 如果缺证据，给后续节点打 warning，不允许盲目生成“完整分析”

#### 3) CompanyProfiler
职责：
- 基于 evidence bundle 生成基础公司画像
- 重点负责：
  - 公司总体做什么
  - 收入模式
  - 主营产品/服务
  - 关键地区

#### 4) BusinessAnalyst
职责：
- 聚焦商业模式、市场空间、业务板块拆解
- 集团公司场景下，负责业务分部分析

#### 5) CultureAnalyst
职责：
- 聚焦企业文化、管理层、薪酬、股权激励、治理稳定性

#### 6) PeerAnalyzer
职责：
- 基于已有 peer candidate + filing / web 证据进行分类和解释
- 输出：
  - core peers
  - secondary peers
  - valuation peers

#### 7) NewsCollector
职责：
- 获取近期新闻
- 做事件抽取与摘要
- 输出 event timeline

#### 8) FinancialAnalyst
职责：
- 对最近 5 年财务数据做趋势分析
- 输出同行比较
- 识别红旗

#### 9) ValuationAgent
职责：
- 输出价值投资与成长股两套估值
- 明确关键假设

#### 10) RiskAnalyst
职责：
- 基于：
  - filing risk factors
  - 财务红旗
  - 新闻事件
  - 治理/管理层信息
  - 当前估值
  输出综合风险评估

#### 11) ReportPlanner
职责：
- 决定最终报告各 section 需要哪些 evidence 和 supporting facts
- 防止 ReportGenerator 自由发挥

#### 12) ReportGenerator
职责：
- 生成结构化 JSON 报告
- 生成 Markdown 报告

#### 13) ReportCoverageChecker
职责：
- 检查是否出现以下问题：
  - 业务分析空泛
  - 证据不足却写得像确定事实
  - 风险未覆盖 filing 风险因素
  - 集团公司没有分板块分析却未提示
- 若不满足质量标准，可要求局部重跑或返回 warnings

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

## 6. Agent / Node 定义

### 6.1 CompanyProfiler
输入：
- ticker
- company_name
- evidence_bundle
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

### 6.2 BusinessAnalyst
输入：
- company_profile
- filing evidence
- segment evidence
- market evidence
- web searching evidence

输出：
- business_analysis
- segment_analysis
- market_space_analysis
- competition_analysis

关键要求：
- 若是集团公司，必须尝试拆解主要业务板块；
- 若缺业务占比，不能编造，但要明确：
  - 已识别哪些业务板块
  - 哪些板块缺少定量占比
  - 各板块的市场情况和竞争情况

---

### 6.3 CultureAnalyst
输入：
- management evidence
- compensation evidence
- equity incentive evidence
- governance evidence
- news / event evidence

输出：
- culture_and_governance_analysis

要求至少覆盖：
- 管理层核心成员
- 管理层稳定性
- 激励机制
- 员工薪酬/股权激励
- 资本配置与治理风格

---

### 6.4 PeerAnalyzer
输入：
- peer candidates
- local knowledge
- filing competition evidence
- web search evidence

输出：
- peer_analysis

要求：
- 不直接凭空生成 peer
- 对已有候选进行：
  - core / secondary / valuation 分类
  - rationale
  - evidence

---

### 6.5 NewsCollector
输入：
- ticker
- peer_list

输出：
- company_news
- peer_news
- event_list
- recent_external_signals

要求：
- 只抓新闻，不生成结论
- 重点产出结构化事件

---

### 6.6 FinancialAnalyst
输入：
- structured financial statements
- base info
- peer list

输出：
- financial_analysis
- financial_red_flags
- peer_comparison

重点：
- gross margin
- net margin
- ROE
- ROIC
- revenue growth
- OCF vs NI
- cash conversion
- capital intensity

---

### 6.7 ValuationAgent
输入：
- company basic info
- financial analysis
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

### 6.8 RiskAnalyst
输入：
- filing risk factors
- event_list
- financial_red_flags
- governance analysis
- valuation analysis

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

### 6.9 ReportGenerator / InvestmentExpert
输入：
- 所有前序结构化结果
- evidence_refs

输出：
- report_json
- report_markdown
- final_rating
- followup suggestions

要求：
- 不能自由发散写长文
- 必须基于已给定 evidence 和 structured outputs 组装

---

### 6.10 FollowupQAAgent
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

### 6.11 FeedbackRefiner
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

---

### 9.2 LLM 调用点建议

#### A. CompanyProfiler
输入：
- evidence bundle
- company basic info

输出：
- structured company profile

#### B. BusinessAnalyst
输入：
- segment / market / competition evidence

输出：
- business analysis
- segment analysis
- market space analysis

#### C. CultureAnalyst
输入：
- management / incentive / governance evidence

输出：
- culture & governance analysis

#### D. PeerAnalyzer
输入：
- peer candidates + evidence

输出：
- peer classification + rationale

#### E. NewsCollector（可选增强）
输入：
- already fetched news list

输出：
- better summary
- event extraction refinement

#### F. RiskAnalyst
输入：
- structured risk inputs + evidence

输出：
- final risk narrative

#### G. ReportGenerator
输入：
- all structured section outputs

输出：
- final report json
- markdown

#### H. FollowupQAAgent
输入：
- question + evidence

输出：
- grounded answer

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

## 12. 增量实施方案

### Phase 1：最小可行增强
目标：
- 提升报告的商业分析与证据覆盖

改动：
- 新增 FilingCoverageChecker
- 新增 BusinessAnalyst / CultureAnalyst
- CompanyProfiler 改成 evidence bundle + gating
- Report JSON schema 升级
- 报告页显示 evidence_refs

验收：
- 商业分析不再轻易出现“信息不足”
- 报告中每个主要判断开始可追溯证据

---

### Phase 2：报告质量增强
目标：
- 集团公司、竞争格局、文化治理分析明显增强

改动：
- 新增 company_segment_profiles
- 新增 management_profiles / incentive_records
- 新增 PeerAnalyzer
- RiskAnalyst 增强 filing 风险证据使用
- ReportCoverageChecker 上线

验收：
- 集团公司可输出分业务板块分析（若证据支持）
- 企业文化部分能覆盖管理层稳定性和激励机制
- 风险分析能引用 filing 风险因素

---

### Phase 3：多轮问答与反馈闭环
目标：
- 从一次性报告生成器升级为持续投研助手

改动：
- 新增 report_sessions / followup_questions / user_feedback
- 新增 FollowupQA Graph
- 新增 FeedbackRefinement Graph
- UI 加入追问区与反馈区

验收：
- 用户可继续追问
- section 可局部重跑
- 反馈可驱动增强

---

### Phase 4：生产化与体验增强
目标：
- 界面、性能、检索、可观测性优化

改动：
- 更强 query planner
- hybrid retrieval
- reranker
- 更多 telemetry / tracing
- UI 交互细化

验收：
- 追问质量提升
- section 精准重跑更稳定
- 日志和监控更完备

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

## 14. 推荐的目标 LangGraph 新流程

### 14.1 主报告流程
```text
START
-> FilingIngestion
-> FilingCoverageChecker
-> CompanyProfiler
-> BusinessAnalyst
-> CultureAnalyst
-> PeerAnalyzer
-> NewsCollector
-> FinancialAnalyst
-> ValuationAgent
-> RiskAnalyst
-> ReportPlanner
-> ReportGenerator
-> ReportCoverageChecker
-> END
```

### 14.2 报告后追问流程
```text
START
-> QueryClassifier
-> QueryPlanner
-> MultiSourceRetriever
-> AnswerComposer
-> EvidenceFormatter
-> END
```

### 14.3 用户反馈增强流程
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

## 17. 推荐优先修改的 Prompt 列表

1. CompanyProfiler Prompt
2. BusinessAnalyst Prompt
3. CultureAnalyst Prompt
4. PeerAnalyzer Prompt
5. RiskAnalyst Prompt
6. ReportGenerator Prompt
7. FollowupQAAgent Prompt
8. FeedbackRefiner Prompt

---

## 18. 最值得优先落地的 10 项改动

1. 新增 `FilingCoverageChecker`
2. 新增 `BusinessAnalyst`
3. 新增 `CultureAnalyst`
4. 新增 `PeerAnalyzer`
5. 升级报告 JSON schema，强制每个 section 带 evidence_refs
6. 新增 `report_sessions`
7. 新增 `followup_questions`
8. 新增 Follow-up QA Graph
9. 报告 UI 增加证据侧栏和追问区
10. 用户反馈驱动的 section 级局部重跑机制
