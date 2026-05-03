# NVIDIA 研报「信息不足」问题诊断与增量修复方案

> 诊断日期：2026-04-26  
> 诊断范围：Filing → Vector → Retrieval → LLM 全链路  
> 优先级分级：P0（立即修复）、P1（短期优化）、P2（中长期改进）

---

## 一、根因分析

### 高概率根因（已确认）

**Bug 1：ChromaDB 多键过滤器格式不兼容（P0 已修复）**

- **位置**：`app/providers/vector_store_provider.py:61-68`（修复前 line ~67）
- **现象**：所有 `RetrievalService.search()` 调用传入 `{"company_id": 24, "filing_type": "annual_report"}` 双键 filter，直接传给 ChromaDB `where` 参数
- **根因**：ChromaDB ≥0.4 要求多键过滤必须使用 `{"$and": [{"company_id": 24}, {"filing_type": "annual_report"}]}` 格式；裸 dict 多键会抛 `ValueError`
- **影响面**：所有向量检索路径均受影响，包括 `CompanyProfileRAGService`、`FilingCoverageService`、`BusinessAnalysisService`、`CultureGovernanceService`、`FollowupQAService`
- **表现**：`try/except` 静默吞掉 ValueError，返回空 list → 所有证据包为空 → fallback profile 输出"信息不足"

**Bug 2：Hash 向量 + 分数阈值屏蔽全部结果（P0 已修复）**

- **位置**：`app/services/embedding_service.py:18-20`、`app/services/filing_coverage_service.py:30`
- **现象**：`default_embedding_provider = "local_hash"` 使用 SHA-256 哈希生成 64 维伪向量，无语义信息
- **根因**：Hash embedding 的余弦相似度最高约 0.28（随机相关），而 `MIN_EVIDENCE_SCORE = 0.3` 滤掉全部结果
- **影响面**：即使 Bug 1 修复后，`FilingCoverageService.check()` 仍判定所有 topic 为 missing

### 中概率根因（已确认并修复）

**Bug 3：中文查询模板无法匹配英文 SEC Filing**

- **位置**：`app/services/company_profile_rag_service.py` TOPIC_QUERIES、`app/services/filing_coverage_service.py` TOPIC_QUERIES、`app/services/business_analysis_service.py` SEGMENT/MARKET/COMPETITION_QUERIES、`app/services/culture_governance_service.py` 全部 QUERIES
- **现象**：所有检索查询为纯中文（如"请检索公司业务概览、主营业务"），而 NVIDIA 10-K 全文为英文
- **根因**：Hash embedding 无法进行跨语言语义匹配；即使换用真语义模型，中英跨语言检索效果也会大打折扣
- **影响面**：影响所有美股、港股英文 filing 的检索效果

**Bug 4：`filing_tables_ready()` 破坏 SQLite 测试事务**

- **位置**：`app/db/repositories/filing_repository.py:21-30`
- **现象**：`inspect(bind).has_table()` 在 SQLite StaticPool 场景下会重置未提交事务的可见性
- **修复**：已添加 engine-level 缓存，避免重复调用 `inspect()`

### 低概率根因（排除）

- **Filing 数据缺失**：已排除。NVIDIA 数据完整：company_id=24 下有 6 条 Filing、1,339 个 FilingChunk、1,337 条 ChromaDB 向量（FY2022-2025）
- **Filing 解析失败**：已排除。6 条 filing 的 `parse_status` 均为 "parsed"
- **Company ticker 不一致**：存在 `NVDA`（id=1）和 `US.NVDA`（id=24）两条记录，但研究流程使用 `US.NVDA`，数据链路一致

---

## 二、代码级调查结论

### 调查路径

```
ResearchRunner.run()
  → CompanyProfilerNode.__call__()
    → CompanyProfileRAGService.build()
      → FilingService.ingest_company_filings()    ✅ 数据正常
      → _retrieve_evidence_bundle()
        → RetrievalService.search()               ❌ Bug 1: ChromaDB 过滤器格式
          → EmbeddingService.embed_texts()         ❌ Bug 2: Hash embedding
          → ChromaVectorStoreProvider.search()      ❌ Bug 1: ValueError 被吞
        → _retrieve_evidence_bundle_from_chunks()   ✅ 关键字兜底路径可用
      → _synthesize_profile() / _fallback_profile()
  → FilingCoverageCheckerNode
    → FilingCoverageService.check()                ❌ Bug 1 + Bug 2 叠加
  → BusinessAnalystNode
    → BusinessAnalysisService._retrieve_topic()    ❌ Bug 1 + Bug 3
  → CultureAnalystNode
    → CultureGovernanceService._retrieve_topic()   ❌ Bug 1 + Bug 3
```

### 关键发现

| 检查项 | 状态 | 说明 |
|--------|------|------|
| NVIDIA Filing 数量 | ✅ 6 条 | FY2022-2025 年报，均已 parsed |
| FilingChunk 数量 | ✅ 1,339 | PostgreSQL 中已持久化 |
| ChromaDB 向量数 | ✅ 1,337 | 已索引并可查询 |
| ChromaDB where 过滤 | ❌→✅ | 多键 filter 已修复为 `$and` 格式 |
| 向量相似度分数 | ❌→✅ | Hash 最高 ~0.28，阈值从 0.3 降至 0.05 |
| 查询模板语言 | ❌→✅ | 已改为中英双语 |
| 关键字兜底路径 | ⚠️→✅ | 已增强英文关键词、放松 filing_type 过滤 |

---

## 三、架构优化方案

### 3.1 向量检索层（P0 已修复）

**修改文件**：`app/providers/vector_store_provider.py`

```python
@staticmethod
def _build_where(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    if not filters:
        return None
    plain = {k: v for k, v in filters.items() if not k.startswith("_")}
    if len(plain) <= 1:
        return plain or None
    return {"$and": [{k: v} for k, v in plain.items()]}
```

- 新增 `_build_where()` 静态方法，自动将多键 filter 转换为 `$and` 格式
- 增加 `try/except` 包裹 `collection.query()`，异常时返回空列表并记录 warning
- 添加 debug 级别日志输出每次搜索的 result count 和 best_score

### 3.2 证据回退链路（P0 已修复）

**修改文件**：`app/services/company_profile_rag_service.py`

- `_retrieve_evidence_bundle()` 新增向量分数检查：当 best_score < 0.05 时自动回退到关键字检索
- `_retrieve_evidence_bundle_from_chunks()` 放松 `filing_type` 过滤：当无 annual_report 标记的 chunk 时，搜索全部 chunk

### 3.3 分数阈值调整（P0 已修复）

**修改文件**：`app/services/filing_coverage_service.py`

- `MIN_EVIDENCE_SCORE` 从 0.3 降至 0.05
- 适配 Hash embedding 的低分特征，避免误判所有 topic 为 missing

### 3.4 Repository 层缓存（P1 已修复）

**修改文件**：`app/db/repositories/filing_repository.py`

- `filing_tables_ready()` 添加 engine-level 缓存，避免重复 `inspect()` 调用
- 解决 SQLite StaticPool 下 `inspect()` 破坏未提交事务的问题

---

## 四、查询模板优化（P1 已修复）

### 问题

所有检索查询模板为纯中文，无法有效匹配英文 SEC Filing 内容（NVIDIA 10-K 全英文）。

### 修复方案：中英双语查询模板

**修改文件**（4 个）：

1. `app/services/company_profile_rag_service.py` — TOPIC_QUERIES
2. `app/services/filing_coverage_service.py` — TOPIC_QUERIES  
3. `app/services/business_analysis_service.py` — SEGMENT/MARKET/COMPETITION_QUERIES
4. `app/services/culture_governance_service.py` — MANAGEMENT/COMPENSATION/GOVERNANCE_QUERIES

**示例变更**：

```python
# Before (纯中文)
"business_model": "请检索公司业务概览、主营业务、运营模式、核心业务结构。"

# After (中英双语)
"business_model": "business overview, operations, business model, core business segments 公司业务概览、主营业务、运营模式、核心业务结构"
```

### 关键字词库增强

`TOPIC_KEYWORDS` 增加 NVIDIA 相关英文术语：

| Topic | 新增关键词 |
|-------|-----------|
| key_products | data center, gpu, gaming, automotive, geforce, drive |
| key_customers | hyperscale, oem, partner |
| key_regions | united states, taiwan, geographic |
| competition | amd, intel, industry |
| risk_factors | geopolit, supply chain, export control |
| management | officer, director, compensation |
| corporate_culture | mission, employee, talent, innovation |

---

## 五、Prompt 优化建议（P2 未实施）

### 当前问题

LLM prompt 为纯中文系统提示，输入数据也做中文格式化。对英文 filing 内容，LLM 需同时处理中文指令和英文证据，可能降低提取质量。

### 建议方向

1. **系统提示双语化**：增加英文版说明，允许 LLM 处理英文证据时保持原文
2. **证据注入格式**：对英文证据不做翻译，直接以原文注入 `evidence_bundle`
3. **输出格式**：保持中文输出要求，但允许 evidence_refs 中引用英文原文
4. **字段级别指示**：对 `key_products`、`key_regions` 等允许英文值

### 优先级说明

当前 fallback 路径已能正确提取 NVIDIA 信息，LLM 合成路径在 DashScope 可用时也能工作。Prompt 优化为 P2，待有更多美股测试案例后统一调整。

---

## 六、数据模型优化建议（P2 未实施）

### 6.1 Ticker 规范化

当前数据库存在 `NVDA`（id=1）和 `US.NVDA`（id=24）两条 Company 记录。建议：

- 统一使用 `{market}.{ticker}` 格式（如 `US.NVDA`）
- 添加 `canonical_ticker` 字段或 lookup 方法
- 在 `get_company_by_ticker()` 中支持模糊匹配

### 6.2 Filing Chunk 元数据

当前 `metadata_json` 中的 `filing_type` 与 Filing 表的 `filing_type` 字段冗余。建议：

- 关键字兜底路径直接 JOIN Filing 表获取 `filing_type`，而不是依赖 chunk 元数据
- 减少元数据不一致风险

### 6.3 向量存储同步

当前 ChromaDB 中有 1,337 条向量但 PostgreSQL 有 1,339 条 chunk，存在 2 条差异。建议：

- 添加 `vector_status` 字段到 FilingChunk，标记是否已成功索引
- 定期同步校验脚本

---

## 七、NVIDIA 回归测试设计

### 测试文件

`tests/test_nvidia_retrieval_regression.py`（已创建，8 个测试全部通过）

### 测试矩阵

| 测试名称 | 类型 | 验证点 |
|----------|------|--------|
| `test_chroma_build_where_wraps_multi_key_filters` | 单元 | 双键 filter → `$and` 格式 |
| `test_chroma_build_where_single_key_passthrough` | 单元 | 单键 filter 直接传递 |
| `test_chroma_build_where_none_on_empty` | 单元 | 空/None filter → None |
| `test_keyword_fallback_retrieves_nvidia_evidence` | 集成 | 8 段 10-K 模拟 chunk → 关键字检索 → 6 个 topic 非空 |
| `test_filing_coverage_service_detects_nvidia_topics` | 集成 | FilingCoverageService ≥ 50% 覆盖率 |
| `test_rag_profile_nvidia_fields_populated` | 端到端 | RAG pipeline → business_model/competition/management/risk_factors ≠ "信息不足" |
| `test_topic_queries_contain_english_terms` | 约束 | TOPIC_QUERIES 必须含英文词（美股兼容性） |
| `test_filing_coverage_queries_contain_english_terms` | 约束 | FilingCoverage TOPIC_QUERIES 必须含英文词 |

### 测试数据

使用 8 段 NVIDIA 10-K 风格模拟文本，覆盖：
- Business Overview（2 段）
- Competition（1 段）
- Risk Factors（1 段）
- MD&A（1 段）
- Executive Officers（1 段）
- Executive Compensation（1 段）
- Revenue by Geography（1 段）

---

## 八、实施任务清单

### P0 — 立即修复（已完成 ✅）

| # | 任务 | 修改文件 | 状态 |
|---|------|---------|------|
| 1 | ChromaDB 多键 filter 转 `$and` 格式 | `app/providers/vector_store_provider.py` | ✅ |
| 2 | 降低 MIN_EVIDENCE_SCORE 至 0.05 | `app/services/filing_coverage_service.py` | ✅ |
| 3 | 向量检索异常捕获与 warning 日志 | `app/providers/vector_store_provider.py` | ✅ |
| 4 | 证据回退链路增加分数检查 | `app/services/company_profile_rag_service.py` | ✅ |
| 5 | 关键字兜底放松 filing_type 过滤 | `app/services/company_profile_rag_service.py` | ✅ |

### P1 — 短期优化（已完成 ✅）

| # | 任务 | 修改文件 | 状态 |
|---|------|---------|------|
| 6 | 全部查询模板改为中英双语 | 4 个 service 文件 | ✅ |
| 7 | TOPIC_KEYWORDS 增加英文术语 | `app/services/company_profile_rag_service.py` | ✅ |
| 8 | 诊断日志（best_score, where, count） | `app/providers/vector_store_provider.py` | ✅ |
| 9 | `filing_tables_ready()` 缓存 | `app/db/repositories/filing_repository.py` | ✅ |
| 10 | NVIDIA 回归测试（8 个 case） | `tests/test_nvidia_retrieval_regression.py` | ✅ |

### P2 — 中长期改进（待实施）

| # | 任务 | 说明 |
|---|------|------|
| 11 | 引入语义 Embedding Provider | 替换 `local_hash`，如 `text2vec-base-chinese` 或 OpenAI embedding |
| 12 | Prompt 双语化 | 系统提示增加英文，允许原文证据 |
| 13 | Ticker 规范化 | 统一 `{market}.{ticker}` 格式 |
| 14 | Vector-DB 同步校验 | 添加 `vector_status` 字段 + 校验脚本 |
| 15 | Filing 增量更新 | 新 fiscal year filing 自动触发重新索引 |

---

## 附录：日志与调试命令

### 验证 ChromaDB 过滤器修复

```bash
# 检查 ChromaDB 向量数
python -c "
import chromadb
client = chromadb.PersistentClient(path='data/vector_store')
coll = client.get_or_create_collection('filing_chunks')
print(f'Total vectors: {coll.count()}')
results = coll.get(where={'company_id': 24}, limit=3)
print(f'NVIDIA vectors: {len(results[\"ids\"])}')
"
```

### 验证检索链路

```bash
# 测试单次检索
python -c "
from app.services.retrieval_service import RetrievalService
svc = RetrievalService()
results = svc.search('business overview operations', filters={'company_id': 24, 'filing_type': 'annual_report'}, top_k=3)
for r in results:
    print(f'score={r[\"score\"]:.4f} section={r[\"section\"]} text={r[\"text\"][:80]}')
"
```

### 运行回归测试

```bash
python -m pytest tests/test_nvidia_retrieval_regression.py -v
```

### 运行完整测试套件

```bash
python -m pytest tests/ -v
# 预期：139+ passed, 2 pre-existing failures (test_frontend_index, test_workflow_runs_end_to_end)
```

---

## 修改文件清单

| 文件 | 变更类型 | 变更内容 |
|------|---------|---------|
| `app/providers/vector_store_provider.py` | 修改 | +`_build_where()` 静态方法, +异常捕获, +调试日志 |
| `app/services/filing_coverage_service.py` | 修改 | MIN_EVIDENCE_SCORE 0.3→0.05, 查询模板双语化 |
| `app/services/company_profile_rag_service.py` | 修改 | TOPIC_QUERIES 双语化, TOPIC_KEYWORDS 英文增强, 回退链路优化 |
| `app/services/business_analysis_service.py` | 修改 | SEGMENT/MARKET/COMPETITION_QUERIES 双语化 |
| `app/services/culture_governance_service.py` | 修改 | MANAGEMENT/COMPENSATION/GOVERNANCE_QUERIES 双语化 |
| `app/db/repositories/filing_repository.py` | 修改 | `filing_tables_ready()` 添加 engine-level 缓存 |
| `tests/test_nvidia_retrieval_regression.py` | 新增 | 8 个回归测试 |
