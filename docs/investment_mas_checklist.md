# Investment MAS 开发验收 Checklist

## 1. 项目基础结构验收

### 1.1 目录结构
检查是否存在以下核心目录与文件：

```text
investment_mas/
  app/
    api/routes/
    core/
    db/models/
    graph/nodes/
    services/
    providers/
    schemas/
    utils/
    main.py
  alembic/
  tests/
  scripts/
  Dockerfile
  docker-compose.yml
  pyproject.toml
  README.md
```

### 1.2 工程规范
检查项：

- 是否使用 Python 3.11+
- 是否使用 FastAPI
- 是否使用 LangGraph
- 是否使用 SQLAlchemy + Alembic
- 是否有类型注解
- 是否有 docstring
- 是否避免把所有逻辑写在单文件里
- 是否具备模块化分层

### 1.3 配置管理
检查项：

- 是否存在统一配置文件，如 `config.py`
- 是否支持通过环境变量配置：
  - `DATABASE_URL`
  - `FINANCIAL_DATA_PROVIDER`
  - `LOCAL_FINANCIAL_BASE_DIR`
  - `NEWS_PROVIDER`
  - `COMPANY_INFO_PROVIDER`
  - `LOG_LEVEL`
- 是否避免在业务逻辑中硬编码路径和数据库连接串

## 2. 数据库设计验收

### 2.1 表模型完整性
检查以下表是否全部实现：

- `companies`
- `company_profiles`
- `peer_mappings`
- `financial_reports`
- `financial_peer_snapshots`
- `news_articles`
- `news_events`
- `risk_assessments`
- `valuation_results`
- `research_reports`

### 2.2 字段完整性
检查项：

- 字段是否与设计文档一致
- 类型是否合理
- 主键、外键是否建立
- 时间字段是否存在
- JSON 字段是否用 JSON / JSONB 之类合理存储

### 2.3 迁移机制
检查项：

- 是否提供 Alembic 配置
- 是否可以正常生成和执行 migration
- migration 后数据库 schema 是否与 model 一致

### 2.4 索引与约束
检查项：

- `companies.ticker` 是否唯一
- 关键查询字段是否加索引
- 关系表是否存在合理外键约束

## 3. Provider 抽象层验收

### 3.1 FinancialDataProvider 抽象
检查项：

- 是否定义抽象基类 `FinancialDataProvider`
- 是否包含统一方法：
  - `load_company_financials`
  - `load_peer_financials`

### 3.2 LocalFinancialDataProvider
检查项：

- 是否默认读取本地目录  
  `/Users/jacky/Desktop/Mypython/stock/finance_report_web`
- 是否支持递归扫描
- 是否支持 csv / xlsx / json
- 是否能识别字段别名
- 是否能标准化输出
- 缺失字段时是否只 warning，不直接崩溃
- 是否通过配置传入 base_dir，而非强耦合硬编码

### 3.3 DatabaseFinancialDataProvider
检查项：

- 是否从 `financial_reports` 表读取
- 是否可作为 fallback provider
- 是否与 Local provider 接口一致

### 3.4 NewsProvider
检查项：

- 是否存在抽象层
- 第一版是否实现 `MockNewsProvider`
- 是否方便替换成真实新闻抓取器

### 3.5 CompanyInfoProvider
检查项：

- 是否存在抽象层
- 第一版是否实现 `MockCompanyInfoProvider`
- 是否方便替换成真实行业/竞对来源

## 4. 本地财务数据解析验收

### 4.1 文件扫描能力
检查项：

- 是否实现递归扫描
- 是否能识别目标目录下的有效文件
- 是否能忽略不支持格式并打日志

### 4.2 文件解析能力
检查项：

- csv 是否能解析
- xlsx 是否能解析
- json 是否能解析
- 解析失败时是否记录 warning/error，而不是全流程中断

### 4.3 字段映射能力
检查项：

- 是否支持字段别名，例如：
  - `revenue / total_revenue / 营业收入`
  - `net_income / 净利润`
  - `gross_margin / 毛利率`
  - `roe / 净资产收益率`
- 是否有独立工具文件负责字段映射
- 是否输出统一 schema

### 4.4 数据完整性与容错
检查项：

- 数据不足 5 年时是否能返回可用数据
- 某些关键字段缺失时是否允许部分分析继续进行
- 是否能根据 ticker/company_name/文件名做合理匹配

## 5. LangGraph 工作流验收

### 5.1 State 设计
检查项：

- 是否存在 `ResearchState`
- 是否包含设计文档要求字段
- 是否每个 node 只修改自己负责的 state 字段
- 是否有 `errors` / `warnings` 机制

### 5.2 Workflow 结构
检查项：

工作流是否严格符合：

```text
START
-> CompanyProfiler
-> NewsCollector
-> FinancialAnalyst
-> ValuationAgent
-> RiskAnalyst
-> InvestmentExpert
-> END
```

### 5.3 Node 职责边界
检查项：

- `CompanyProfiler` 是否只负责公司画像与竞对
- `NewsCollector` 是否只负责新闻与事件抽取
- `FinancialAnalyst` 是否只负责财务分析与同行对比
- `ValuationAgent` 是否只负责估值
- `RiskAnalyst` 是否只负责风险评估
- `InvestmentExpert` 是否只负责最终报告整合

### 5.4 容错性
检查项：

- 单个节点失败时是否会记录错误
- 是否尽量返回部分结果
- 是否避免一个 provider 异常导致整个流程必然崩溃

## 6. Service 层验收

### 6.1 financial_service
检查项：

- 是否通过 `FinancialDataProvider` 获取数据，而不是直接读文件
- 是否能计算：
  - 最近 5 年财务汇总
  - revenue CAGR
  - gross margin trend
  - net margin trend
  - ROE trend
  - OCF vs Net Income
- 是否输出 `financial_summary`
- 是否识别 `financial_red_flags`

### 6.2 valuation_service
检查项：

- 是否将两种估值逻辑分开实现
- 是否支持：
  - 价值投资估值
  - 成长股估值
- 是否输出统一结构：
  - bull
  - base
  - bear
  - assumptions

### 6.3 risk_service
检查项：

- 是否实现规则优先风险评分
- 是否输出：
  - fundamentals_risk_score
  - competition_risk_score
  - governance_risk_score
  - event_risk_score
  - valuation_risk_score
  - overall_risk_score
- 是否输出 red_flags / key_risks / monitoring_signals / recommendation
- 是否保留未来配置化和 LLM 增强空间

### 6.4 event_extraction_service
检查项：

- 是否支持基于标题/摘要关键词识别事件
- 是否至少支持：
  - guidance_cut
  - lawsuit
  - regulatory_action
  - management_change
  - margin_pressure
  - demand_slowdown
- 是否输出标准事件结构

### 6.5 report_service
检查项：

- 是否能从 state 组装最终报告
- 是否同时输出：
  - Markdown 报告
  - JSON 结构化结果
- 是否章节完整

## 7. 业务逻辑验收

### 7.1 公司画像分析
检查项：

- 是否输出：
  - business_model
  - market_space
  - competition_landscape
  - corporate_culture
- 第一版即便用 mock，也要结构合理、可替换

### 7.2 财务分析
检查项：

- 是否能正确处理近 5 年数据
- 是否能发现明显红旗
- 是否有趋势判断，而不仅是原始数字罗列

### 7.3 同行对比
检查项：

- 是否至少比较：
  - gross_margin
  - net_margin
  - roe
  - revenue_growth
- 是否输出公司值、同行均值/中位数、排序或分位

### 7.4 估值输出
检查项：

- 是否有 bull/base/bear
- 是否有 assumptions
- 是否区分 value-style 和 growth-style

### 7.5 风险输出
检查项：

- 是否可解释
- 是否能和财务、新闻、估值对应起来
- 是否不是“拍脑袋分数”

## 8. API 接口验收

### 8.1 GET /health
检查项：

- 是否正常返回：
  - service
  - status
  - time

### 8.2 POST /research/run
检查项：

- 是否接受：
```json
{
  "ticker": "NVDA",
  "company_name": "NVIDIA"
}
```

- 是否能触发完整 LangGraph 工作流
- 是否返回：
  - ticker
  - company_name
  - final_rating
  - executive_summary
  - report_markdown
  - report_json

### 8.3 GET /research/{ticker}/latest
检查项：

- 是否能返回最近一次研究报告
- 若不存在数据，是否返回合理 404 或空结果说明

### 8.4 错误处理
检查项：

- 是否有统一错误结构
- 是否返回清晰 message
- 是否避免 traceback 直接暴露给用户

## 9. 报告输出验收

### 9.1 Markdown 报告
检查项：

最终报告是否至少包含：

1. Executive Summary  
2. Business Analysis  
3. Financial Analysis  
4. Peer Comparison  
5. Valuation  
6. Risk Review  
7. Final Recommendation  

### 9.2 JSON 报告
检查项：

- 是否结构清晰
- 是否便于前端展示或后续存档
- 是否包含 errors / warnings

### 9.3 最终评级
检查项：

`final_rating` 是否限定为：

- `buy`
- `watch`
- `avoid`
- `research_more`

## 10. 测试验收

### 10.1 单元测试覆盖
检查项：

是否存在并可运行：

- `test_health.py`
- `test_workflow.py`
- `test_valuation_service.py`
- `test_risk_service.py`
- `test_local_financial_provider.py`

### 10.2 关键测试点
检查项：

- LocalFinancialDataProvider 能否解析样例数据
- 风险规则是否按预期计算
- 估值输出结构是否稳定
- Workflow 是否能在 mock 数据下完整跑通
- API 是否可正常返回

### 10.3 最低验收标准
至少要满足：

- `pytest` 可执行
- 核心测试通过
- 主流程可运行
- 不因为本地目录某个文件异常而整体失败

## 11. 部署与运行验收

### 11.1 本地运行
检查项：

- 是否能本地启动 FastAPI
- 是否能连接 PostgreSQL
- 是否能读取本地财报目录
- 是否能生成研究报告

### 11.2 Docker
检查项：

- 是否存在 `Dockerfile`
- 是否存在 `docker-compose.yml`
- 是否支持挂载本地财报目录
- 是否能用 compose 启动 app + postgres

### 11.3 README
检查项：

README 是否清楚说明：

- 如何安装依赖
- 如何配置数据库
- 如何执行 migration
- 如何配置本地财报目录
- 如何 seed demo data
- 如何启动服务
- 如何调用 API
- 如何用 Docker 跑起来
- ECS 部署思路

## 12. 代码质量验收

### 12.1 可维护性
检查项：

- 模块边界清晰
- 无明显重复逻辑
- Provider / Service / Node 职责清楚
- 配置与业务分离

### 12.2 可扩展性
检查项：

- 替换新闻源是否容易
- 替换公司画像来源是否容易
- 替换财报数据源是否容易
- 引入 LLM 是否容易
- 引入向量库是否容易

### 12.3 可读性
检查项：

- 命名清晰
- 函数长度合理
- 关键逻辑有注释/docstring
- 错误日志信息清楚

## 13. 最终验收结论模板

Claude Code 可以按下面格式输出 review 结论：

```text
【Investment MAS 验收结论】

1. 总体结论
- 通过 / 有条件通过 / 不通过

2. 已满足项
- ...
- ...

3. 主要问题
- ...
- ...

4. 高优先级修复项
- ...
- ...

5. 中优先级优化项
- ...
- ...

6. 风险提示
- ...
- ...

7. 建议下一步
- 先修复 Provider 抽象问题
- 再补 workflow 容错
- 最后完善测试和 Docker 部署
```

## 14. 给 Claude Code 的一句话任务指令

```text
请基于docs目录下的design spec和checklist，对当前项目进行系统 review，并且输出可以直接复制给codex优化改进建议方案。重点检查：架构分层、Provider 抽象、LangGraph workflow、数据库设计、API 返回结构、本地财报目录解析鲁棒性、估值与风险逻辑是否符合设计。请输出：1）通过/不通过结论；2）问题清单（按高/中/低优先级）；3）具体修改建议；4）缺失测试项；5）是否满足 MVP 验收标准。
```
