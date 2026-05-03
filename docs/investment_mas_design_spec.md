# 投资研究 MAS 设计方案文档

## 1. 文档目的

本文档用于作为 **Investment Research MAS（Multi-Agent System）** 的统一设计依据，供 Codex 与 Claude Code 用于：

- 开发实现
- 代码审查
- 测试验证
- 接口联调
- 部署落地

本文档覆盖但不限于以下内容：

- 产品目标与范围
- 总体架构设计
- 模块职责划分
- LangGraph 工作流设计
- Provider 抽象设计
- 数据模型与数据库设计
- API 接口说明
- 核心算法与规则说明
- 容错与降级策略
- 部署方案
- 测试与验收标准
- 后续演进路线

---

## 2. 项目目标与范围

### 2.1 项目目标

构建一个可运行的投资研究 MAS，输入股票代码（ticker）和公司名称后，系统自动完成以下任务：

1. 从 **商业模式、市场空间、竞争格局、企业文化** 等维度进行系统分析。
2. 读取最近 **5 年财务报表数据**，并与主要竞争对手对比关键指标，如：
   - 毛利率
   - 净利率
   - ROE
   - 营收增速
   - 经营现金流与净利润匹配度
3. 基于财务数据输出估值结果，支持两套估值框架：
   - 巴菲特价值投资风格估值
   - 高科技成长股估值
4. 获取公司新闻、公告、电话会、财经资讯等信息，并结合近 5 年财报进行风险评估。
5. 调用阿里云 DashScope / Qwen 模型对多源信息做综合评估。
6. 输出一份结构化投资研究报告，供投资决策参考。
7. 第一版支持本地运行，后续支持通过 Docker 部署到阿里云 ECS。

### 2.2 MVP 范围

第一版（MVP）聚焦：

- 单公司研究
- 近 5 年财务数据分析
- 简化的竞对映射
- Mock 新闻源 / 可替换真实抓取器
- 规则优先的风险分析
- Markdown + JSON 报告输出
- API 化调用

### 2.3 非目标

第一版暂不纳入：

- 实时行情交易系统
- 自动下单
- 复杂量化因子回测
- 多租户权限体系
- 实时分布式任务编排
- 复杂 Agent 自主协商机制

---

## 3. 关键约束

### 3.1 财报数据来源约束

第一版财报数据 **不实现网页抓取**，直接读取本地目录：

`/Users/jacky/Desktop/Mypython/stock/finance_report_web`

要求：

- 递归扫描目录
- 识别 `csv / xlsx / json`
- 尽量容忍字段命名不一致
- 支持字段别名映射与标准化
- 可按 `ticker/company_name` 获取最近 5 年数据
- 数据不完整时尽量返回部分结果而非整体失败

### 3.2 工程约束

- Python 3.11+
- LangGraph 编排
- FastAPI API 层
- PostgreSQL 持久化
- SQLAlchemy + Alembic
- Provider 抽象层必须清晰，避免硬耦合
- 所有关键函数带类型注解
- 支持 Docker 化

### 3.3 网络与模型约束

- 财报继续使用本地目录，不切换到网页抓取
- 新闻/公告/公开信息允许通过网络抓取
- 大模型能力优先通过阿里云 DashScope OpenAI 兼容接口接入
- DashScope 配置需兼容 `/Users/jacky/Ai-code/MAS` 中已使用的 `llm_config` JSON 结构
- 兼容字段至少包括 `name / provider / base_url / api_key / model / temperature`
- 支持通过本项目环境变量覆盖 JSON 配置，便于本地与 ECS 共用一套配置格式
- 没有配置模型 key 时系统必须自动降级，不中断主流程

---

## 4. 总体架构设计

### 4.1 架构原则

本系统采用：

- **Supervisor + Specialist Agents**
- **Provider 抽象层**
- **结构化数据库 + 原始数据解析层**
- **规则引擎优先，LLM 可选增强**
- **分层解耦，方便替换 Mock / 本地 / 远程能力**

### 4.2 架构分层

系统分为 7 层：

1. **API 接入层**
   - FastAPI 路由
   - 请求验证
   - 响应输出

2. **Orchestration 层**
   - LangGraph 主流程
   - State 管理
   - 节点编排

3. **Agent / Node 层**
   - CompanyProfiler
   - NewsCollector
   - FinancialAnalyst
   - ValuationAgent
   - RiskAnalyst
   - InvestmentExpert

4. **Service 业务层**
   - company_profile_service
   - news_service
   - event_extraction_service
   - financial_service
   - valuation_service
   - risk_service
   - report_service

5. **Provider 数据接入层**
   - FinancialDataProvider
   - LocalFinancialDataProvider
   - DatabaseFinancialDataProvider
   - NewsProvider
   - CompanyInfoProvider
   - Aliyun / DashScope LLM integration
   - DashScopeLLMConfig loader（支持 env 和 MAS `llm_config` JSON）

6. **Persistence 层**
   - PostgreSQL
   - SQLAlchemy Models
   - Repository（可选）

7. **Utility / Parsing 层**
   - 文件扫描
   - 财务字段映射
   - CSV/XLSX/JSON 解析

### 4.3 高层架构图（逻辑）

```text
[Client / User]
      |
      v
[FastAPI API Layer]
      |
      v
[LangGraph Workflow / Supervisor]
      |
      +--> [CompanyProfiler Node] --> [CompanyInfoProvider] --> [Wikipedia / public web]
      |
      +--> [NewsCollector Node] --> [NewsProvider] --> [Google News RSS / SEC EDGAR] --> [EventExtractionService]
      |
      +--> [FinancialAnalyst Node] --> [FinancialDataProvider]
      |                                   |-- LocalFinancialDataProvider
      |                                   |-- DatabaseFinancialDataProvider
      |
      +--> [ValuationAgent Node] --> [ValuationService]
      |
      +--> [RiskAnalyst Node] --> [RiskService]
      |
      +--> [InvestmentExpert Node] --> [ReportService] --> [DashScope / Qwen]
      |
      v
[PostgreSQL / research_reports / risk_assessments / valuation_results ...]
```

---

## 5. 项目目录设计

```text
investment_mas/
  app/
    api/
      routes/
        research.py
        health.py
    core/
      config.py
      logging.py
      database.py
    db/
      base.py
      models/
      repositories/
    graph/
      state.py
      workflow.py
      nodes/
        company_profiler.py
        news_collector.py
        financial_analyst.py
        valuation_agent.py
        risk_analyst.py
        investment_expert.py
    services/
      company_profile_service.py
      news_service.py
      event_extraction_service.py
      financial_service.py
      valuation_service.py
      risk_service.py
      report_service.py
    providers/
      financial_data_provider.py
      news_provider.py
      company_info_provider.py
    schemas/
      research.py
      company.py
      financial.py
      valuation.py
      risk.py
      report.py
    utils/
      file_discovery.py
      financial_mapping.py
      financial_parser.py
    main.py
  alembic/
  tests/
    test_health.py
    test_workflow.py
    test_valuation_service.py
    test_risk_service.py
    test_local_financial_provider.py
  scripts/
    seed_demo_data.py
  Dockerfile
  docker-compose.yml
  pyproject.toml
  README.md
```

---

## 6. LangGraph 工作流设计

### 6.1 主流程

```text
START
  -> CompanyProfiler
  -> NewsCollector
  -> FinancialAnalyst
  -> ValuationAgent
  -> RiskAnalyst
  -> InvestmentExpert
END
```

### 6.2 节点职责概览

#### 6.2.1 CompanyProfiler
输入：
- ticker
- company_name

输出：
- company_profile
- peer_list

职责：
- 生成公司基础画像
- 识别行业、赛道、核心业务
- 输出第一版竞对列表

#### 6.2.2 NewsCollector
输入：
- ticker
- company_name
- peer_list

输出：
- company_news
- peer_news
- event_list

职责：
- 拉取或读取公司/竞对新闻
- 摘要化
- 事件抽取

#### 6.2.3 FinancialAnalyst
输入：
- ticker
- company_name
- peer_list

输出：
- financial_summary
- peer_comparison
- financial_red_flags

职责：
- 从本地目录读取近 5 年财务数据
- 计算关键趋势指标
- 对同行进行关键指标对比

#### 6.2.4 ValuationAgent
输入：
- company_profile
- financial_summary
- peer_comparison

输出：
- valuation_result
- valuation_assumptions

职责：
- 输出价值投资估值
- 输出成长股估值
- 给出 bull/base/bear 区间

#### 6.2.5 RiskAnalyst
输入：
- event_list
- financial_summary
- peer_comparison
- financial_red_flags
- valuation_result

输出：
- risk_assessment

职责：
- 基于规则计算多维风险评分
- 给出红旗事项与跟踪信号

#### 6.2.6 InvestmentExpert
输入：
- 所有前序节点结果

输出：
- final_report
- final_rating

职责：
- 汇总形成最终投资研究报告
- 输出结论与建议

### 6.3 State 设计

建议使用 `TypedDict` 或 `Pydantic` 定义：

```python
class ResearchState(TypedDict, total=False):
    ticker: str
    company_name: str

    company_profile: dict
    peer_list: list[dict]

    company_news: list[dict]
    peer_news: list[dict]
    event_list: list[dict]

    financial_summary: dict
    peer_comparison: dict
    financial_red_flags: list[str]

    valuation_result: dict
    valuation_assumptions: dict

    risk_assessment: dict

    final_report: str
    final_rating: str

    errors: list[str]
```

### 6.4 状态管理原则

- 每个节点只更新自己负责的字段
- 节点失败时尽量记录到 `errors`
- 不因单个模块失败导致整条链必然中断
- 后续可扩展为 LangGraph persistence / checkpoints

---

## 7. Provider 抽象设计

### 7.1 设计目标

Provider 层负责隔离“数据从哪里来”，使业务逻辑不直接依赖：

- 本地文件
- 数据库
- Mock 数据
- 未来远程 API
- 未来网站抓取

### 7.2 FinancialDataProvider

#### 抽象接口

```python
class FinancialDataProvider(ABC):
    @abstractmethod
    def load_company_financials(self, ticker: str, company_name: str) -> list[dict]:
        ...

    @abstractmethod
    def load_peer_financials(self, peer_tickers: list[str]) -> dict[str, list[dict]]:
        ...
```

#### 具体实现

##### LocalFinancialDataProvider
职责：
- 从本地目录读取财报文件
- 递归扫描
- 自动识别格式
- 标准化字段
- 返回统一结构

##### DatabaseFinancialDataProvider
职责：
- 从 PostgreSQL `financial_reports` 表读取数据
- 作为 fallback provider

### 7.3 NewsProvider

抽象接口：
- `load_company_news(...)`
- `load_peer_news(...)`

第一版实现：
- `MockNewsProvider`

后续扩展：
- RSSNewsProvider
- WebScrapeNewsProvider
- FinanceAPIProvider

### 7.4 CompanyInfoProvider

抽象接口：
- `get_company_profile(...)`
- `get_peer_list(...)`

第一版实现：
- `MockCompanyInfoProvider`

后续扩展：
- 基于数据库
- 基于知识库
- 基于 LLM + 搜索

---

## 8. 本地财务数据解析设计

### 8.1 数据读取策略

默认基目录：

`/Users/jacky/Desktop/Mypython/stock/finance_report_web`

读取流程：

1. 递归扫描目录
2. 识别可解析文件
3. 根据文件名与内容判断是否匹配目标公司
4. 解析 CSV / XLSX / JSON
5. 标准化字段
6. 合并为统一财务记录集
7. 按年份筛选近 5 年数据

### 8.2 支持文件格式

- `.csv`
- `.xlsx`
- `.json`

非支持格式：
- 忽略
- 记录 warning log

### 8.3 字段映射策略

需支持字段别名，例如：

- revenue / total_revenue / 营业收入
- net_income / 净利润
- gross_margin / 毛利率
- roe / 净资产收益率
- operating_cash_flow / 经营现金流

### 8.4 标准财务记录结构

推荐统一成：

```json
{
  "ticker": "NVDA",
  "company_name": "NVIDIA",
  "fiscal_year": 2024,
  "period_type": "annual",
  "fiscal_period": "FY",
  "report_date": "2024-01-31",
  "revenue": 1000000,
  "gross_profit": 700000,
  "net_income": 300000,
  "operating_cash_flow": 320000,
  "free_cash_flow": 280000,
  "gross_margin": 0.70,
  "net_margin": 0.30,
  "roe": 0.25,
  "raw_source": {...}
}
```

### 8.5 容错策略

- 缺失字段：允许部分缺失
- 不可解析文件：跳过并记录
- 公司匹配模糊：可基于 ticker、company_name、文件名联合判断
- 数据不足 5 年：尽量使用可用年份，并在 `errors/warnings` 中注明

---

## 9. 数据库设计

### 9.1 设计原则

- 原始数据与结构化分析结果分离
- 报告可回溯
- 支持后续审计与重跑
- 支持多次研究结果留痕

### 9.2 数据表总览

1. `companies`
2. `company_profiles`
3. `peer_mappings`
4. `financial_reports`
5. `financial_peer_snapshots`
6. `news_articles`
7. `news_events`
8. `risk_assessments`
9. `valuation_results`
10. `research_reports`

### 9.3 表设计详述

#### 9.3.1 companies

用途：
- 存放公司基础信息主数据

关键字段：
- `ticker`
- `company_name`
- `market`
- `sector`
- `industry`
- `sub_industry`

#### 9.3.2 company_profiles

用途：
- 保存某日期的公司画像分析结果

说明：
- 支持多版本快照

#### 9.3.3 peer_mappings

用途：
- 记录目标公司与竞对关系

说明：
- `peer_type` 支持 `core / secondary / valuation`

#### 9.3.4 financial_reports

用途：
- 持久化财务数据
- 未来可将本地读取结果入库

#### 9.3.5 financial_peer_snapshots

用途：
- 保存某次同行对比指标结果

#### 9.3.6 news_articles

用途：
- 保存新闻原文与元数据

#### 9.3.7 news_events

用途：
- 保存从新闻中抽取出的结构化事件

说明：
- 风险分析应基于 `news_events`，而不是直接基于原文

#### 9.3.8 risk_assessments

用途：
- 保存结构化风险评估结果

#### 9.3.9 valuation_results

用途：
- 保存估值结果与假设

#### 9.3.10 research_reports

用途：
- 保存最终研究报告

### 9.4 主键与外键关系

- `company_profiles.company_id -> companies.id`
- `peer_mappings.company_id -> companies.id`
- `peer_mappings.peer_company_id -> companies.id`
- `financial_reports.company_id -> companies.id`
- `news_articles.company_id -> companies.id`
- `news_events.article_id -> news_articles.id`
- `news_events.company_id -> companies.id`
- `risk_assessments.company_id -> companies.id`
- `valuation_results.company_id -> companies.id`
- `research_reports.company_id -> companies.id`

### 9.5 索引建议

建议索引：

- `companies.ticker` 唯一索引
- `financial_reports(company_id, fiscal_year, period_type)`
- `news_articles(company_id, published_at)`
- `news_events(company_id, event_date)`
- `research_reports(company_id, report_date)`

---

## 10. API 接口设计

### 10.1 GET /health

#### 说明
健康检查接口。

#### 响应示例

```json
{
  "service": "investment_mas",
  "status": "ok",
  "time": "2026-04-19T10:00:00Z"
}
```

### 10.2 POST /research/run

#### 说明
启动一次完整投资研究流程。

#### 请求体

```json
{
  "ticker": "NVDA",
  "company_name": "NVIDIA"
}
```

#### 处理流程

1. 校验参数
2. 初始化 ResearchState
3. 运行 LangGraph 主流程
4. 持久化估值、风险、报告结果
5. 返回最终输出

#### 响应示例

```json
{
  "ticker": "NVDA",
  "company_name": "NVIDIA",
  "final_rating": "watch",
  "executive_summary": "公司具备较强的增长能力，但估值风险较高。",
  "report_markdown": "# Executive Summary ...",
  "report_json": {
    "business_analysis": {},
    "financial_analysis": {},
    "valuation": {},
    "risk_review": {}
  }
}
```

### 10.3 GET /research/{ticker}/latest

#### 说明
查询某股票最近一次研究报告。

#### 响应示例

```json
{
  "ticker": "NVDA",
  "report_date": "2026-04-19",
  "final_rating": "watch",
  "executive_summary": "...",
  "report_markdown": "...",
  "report_json": {...}
}
```

### 10.4 错误返回规范

统一返回：

```json
{
  "error": {
    "code": "RESEARCH_RUN_FAILED",
    "message": "Failed to run workflow",
    "details": []
  }
}
```

---

## 11. 核心业务逻辑设计

### 11.1 Company Profile 分析逻辑

第一版以 mock / placeholder 为主，但结构上需支持未来替换。

输出内容包括：
- business_model
- market_space
- competition_landscape
- corporate_culture
- key_products
- key_regions

### 11.2 财务分析逻辑

至少实现：

- 近 5 年收入序列
- Revenue CAGR
- 毛利率趋势
- 净利率趋势
- ROE 趋势
- OCF / Net Income 匹配度
- 财务红旗识别

#### 财务红旗示例

- 收入连续 2 年增速下降
- 毛利率显著下行
- 经营现金流持续低于净利润
- ROE 持续下滑

### 11.3 竞对对比逻辑

至少对比：

- gross_margin
- net_margin
- roe
- revenue_growth

输出：
- 公司值
- 同行均值
- 同行中位数
- 排名 / 分位

### 11.4 估值逻辑

#### 11.4.1 价值投资估值

可选简化公式：

- 使用 `owner_earnings` 或 `free_cash_flow`
- 给出保守 / 中性 / 乐观三情景

输出字段：
- bull_value
- base_value
- bear_value
- assumptions

#### 11.4.2 成长股估值

可采用：

- Revenue Growth + Margin Expansion + Exit Multiple
- 或 PS / EV/Sales 简化估值

输出字段同上。

### 11.5 风险评估逻辑

采用“规则优先”策略。

#### 风险维度

- fundamentals_risk_score
- competition_risk_score
- governance_risk_score
- event_risk_score
- valuation_risk_score
- overall_risk_score

#### 规则示例

- 最近两年收入增速连续下降 -> fundamentals risk +X
- 毛利率趋势明显下滑 -> fundamentals risk +X
- OCF 长期弱于净利润 -> fundamentals risk +X
- 同行对比显著落后 -> competition risk +X
- 出现 `guidance_cut / lawsuit / management_change / regulatory_action` -> event risk +X
- 当前估值超出 bull/base 区间过多 -> valuation risk +X

#### 输出内容

- 风险分数
- red_flags
- key_risks
- monitoring_signals
- recommendation

### 11.6 报告生成逻辑

最终报告至少包含：

1. Executive Summary
2. Business Analysis
3. Financial Analysis
4. Peer Comparison
5. Valuation
6. Risk Review
7. Final Recommendation

同时输出：
- Markdown 报告
- JSON 结构化结果

---

## 12. 事件抽取设计

### 12.1 目标

将新闻原文转化为结构化事件，供风险分析使用。

### 12.2 第一版事件类型

- guidance_cut
- guidance_raise
- management_change
- lawsuit
- regulatory_action
- product_launch
- product_delay
- supply_chain_issue
- major_customer_change
- margin_pressure
- demand_slowdown
- financing_activity
- acquisition

### 12.3 抽取方式

第一版采用：
- 标题关键词规则
- 摘要文本关键词规则
- Mock 辅助

### 12.4 输出结构

```json
{
  "event_type": "guidance_cut",
  "sentiment": "negative",
  "importance_score": 0.82,
  "confidence_score": 0.75,
  "risk_tags": ["growth", "demand"],
  "entities": ["NVIDIA"],
  "event_summary": "Company lowered next quarter guidance.",
  "impact_dimensions": ["growth", "valuation"]
}
```

---

## 13. 容错与降级策略

### 13.1 总体原则

- 尽量部分成功，不要全量失败
- 所有失败尽量记录到 `errors`
- 用户能拿到“尽可能完整”的结果

### 13.2 典型场景

#### 本地目录不存在
- 记录 warning
- 尝试数据库 fallback
- 若仍失败，财务分析输出空结果 + 错误说明

#### 财务字段缺失
- 保留现有字段
- 不能计算的指标置空
- 报告中说明数据不完整

#### 新闻源不可用
- 使用 MockNewsProvider
- 风险分析退化为财务+估值驱动

#### Company Profile 数据不足
- 使用 placeholder 内容
- 保持流程继续执行

#### 单节点异常
- 写入 `errors`
- 尝试不中断后续节点（视依赖情况）

---

## 14. 配置设计

建议配置项：

- `DATABASE_URL`
- `FINANCIAL_DATA_PROVIDER=local|database`
- `LOCAL_FINANCIAL_BASE_DIR`
- `NEWS_PROVIDER=mock`
- `COMPANY_INFO_PROVIDER=mock`
- `LOG_LEVEL`
- `APP_ENV`

示例：

```env
DATABASE_URL=postgresql+psycopg://jacky@localhost:5432/investment_mas
FINANCIAL_DATA_PROVIDER=local
LOCAL_FINANCIAL_BASE_DIR=/Users/jacky/Desktop/Mypython/stock/finance_report_web
NEWS_PROVIDER=mock
COMPANY_INFO_PROVIDER=mock
LOG_LEVEL=INFO
```

---

## 15. 部署设计

### 15.1 本地开发部署

- Python 虚拟环境
- 本机 PostgreSQL
- Uvicorn / FastAPI
- 本地文件目录读取财报

### 15.2 Docker 部署

建议：

- `app` 容器
- `postgres` 容器

本地目录财报数据可通过 volume 挂载：

```yaml
volumes:
  - /Users/jacky/Desktop/Mypython/stock/finance_report_web:/data/finance_report_web
```

配置中将 `LOCAL_FINANCIAL_BASE_DIR=/data/finance_report_web`

### 15.3 阿里云 ECS 部署

建议第一版：

- 单台 ECS
- Docker Compose 部署
- Nginx（可后续增加）
- PostgreSQL 可本机容器或云数据库

部署组件：
- investment-mas app
- postgres
- 可选 nginx

---

## 16. 测试设计

### 16.1 单元测试

至少包含：

- `test_health.py`
- `test_local_financial_provider.py`
- `test_valuation_service.py`
- `test_risk_service.py`
- `test_workflow.py`

### 16.2 测试重点

#### LocalFinancialDataProvider
- 能扫描目录
- 能识别 csv/xlsx/json
- 字段映射正确
- 缺失字段时不崩溃

#### ValuationService
- 输出结构完整
- bull/base/bear 存在
- 缺失部分输入时合理退化

#### RiskService
- 规则打分正确
- 红旗事项可解释

#### Workflow
- 主流程可跑通
- 在 provider 返回 mock 数据时可生成报告

### 16.3 验收标准

系统验收至少满足：

1. 服务能启动
2. `/health` 返回正常
3. `/research/run` 能返回结果
4. 能从本地目录读取至少一部分财务数据
5. 能生成结构化报告
6. 数据不足时能返回部分结果和错误说明

---

## 17. 开发里程碑建议

### Phase 1：基础骨架
- 项目目录
- FastAPI
- Config
- DB models
- LangGraph state / workflow

### Phase 2：Provider 与解析层
- LocalFinancialDataProvider
- financial_parser
- financial_mapping
- MockNewsProvider
- MockCompanyInfoProvider

### Phase 3：Service 层
- financial_service
- valuation_service
- risk_service
- report_service
- event_extraction_service

### Phase 4：节点接入与 API 打通
- 各 LangGraph node
- `/research/run`
- 报告落库

### Phase 5：测试与部署
- 单元测试
- Dockerfile
- docker-compose
- README

---

## 18. 后续演进路线

### 18.1 第二版建议

- 接入真实新闻抓取
- 接入真实公司画像来源
- 财报读取结果入库
- 增加异步任务机制
- 引入向量库与知识库
- 风险评估加入 LLM 解释层

### 18.2 第三版建议

- 多公司批量研究
- 定时更新
- 报告对比
- Web 前端
- 用户配置化估值参数

---

## 19. 交付要求总结

Codex / Claude Code 在实现时应严格遵守：

1. 以本文档为唯一设计依据之一。
2. 优先实现 MVP 可运行闭环。
3. 所有模块解耦，不直接把本地文件解析写死在业务逻辑中。
4. Provider 层必须可替换。
5. 财务数据不足时要返回部分结果，不得轻易整体失败。
6. 研究报告必须同时输出 Markdown 与 JSON。
7. 所有核心代码带类型注解与 docstring。
8. 所有数据库模型与 migration 保持一致。
9. 测试需覆盖 Provider、Workflow、Valuation、Risk 等关键模块。
10. README 需完整说明本地运行与 Docker 部署方式。

---

## 20. 附：建议的最终报告 JSON 结构

```json
{
  "ticker": "NVDA",
  "company_name": "NVIDIA",
  "executive_summary": "...",
  "business_analysis": {
    "business_model": "...",
    "market_space": "...",
    "competition_landscape": "...",
    "corporate_culture": "..."
  },
  "financial_analysis": {
    "revenue_cagr": 0.25,
    "gross_margin_trend": "improving",
    "net_margin_trend": "improving",
    "roe_trend": "stable",
    "red_flags": []
  },
  "peer_comparison": {
    "gross_margin": {},
    "net_margin": {},
    "roe": {}
  },
  "valuation": {
    "value_style": {
      "bull": 100,
      "base": 80,
      "bear": 60
    },
    "growth_style": {
      "bull": 120,
      "base": 95,
      "bear": 70
    },
    "assumptions": {}
  },
  "risk_review": {
    "fundamentals_risk_score": 30,
    "competition_risk_score": 40,
    "governance_risk_score": 20,
    "event_risk_score": 35,
    "valuation_risk_score": 60,
    "overall_risk_score": 38,
    "red_flags": [],
    "key_risks": [],
    "monitoring_signals": []
  },
  "final_recommendation": {
    "rating": "watch",
    "reason": "..."
  },
  "errors": []
}
```
