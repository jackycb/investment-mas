# Investment MAS Codex 改进优化方案 V2

> 本文档基于 V2 代码（含 Web 前端、阿里 DashScope/Qwen 集成、WebCompanyInfoProvider、WebNewsProvider）的系统 review 生成。
> 上一轮 review 发现的 P0 问题（ResearchState total=False、节点容错、report_json 传递、缺失测试）已全部修复。
> 本轮聚焦：**运行时错误修复、Web 前端健壮性、LLM 集成优化、Provider 鲁棒性、新模块测试覆盖**。

---

## 当前状态

- 41 个测试全部通过
- 7 层架构完整、Provider 抽象到位、LangGraph 6 节点全部带容错
- 新增：Web 前端 (`app/web/index.html`)、AliyunLLMService (`app/services/aliyun_llm_service.py`)、WebCompanyInfoProvider、WebNewsProvider
- 已知运行时问题需修复

---

## 运行日志分析

用户提供的运行日志揭示以下问题：

```
1. GET /favicon.ico → 404 Not Found
2. Wikipedia API → 403 Forbidden（"Tecent" 拼写错误，应为 "Tencent"）
3. Wikipedia 被调用两次（get_company_profile + get_peers 各调一次）
4. MockCompanyInfoProvider 生成假 peer ticker（HK.00700P1, HK.00700P2）
5. 假 peer ticker 导致 Google News 和本地财报搜索全部无效
6. DashScope 配置被加载两次（日志重复）
```

---

## 任务 1：修复 favicon.ico 404

**文件**: `app/main.py`

浏览器自动请求 `/favicon.ico`，当前未处理。在 `app/web/` 目录放一个 favicon 或返回空响应。

**在 `app/main.py` 中添加**（在 `index()` 函数之后）:

```python
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Return 204 No Content for favicon requests to avoid noisy 404 logs."""
    from fastapi.responses import Response
    return Response(status_code=204)
```

---

## 任务 2：WebCompanyInfoProvider 避免重复 Wikipedia + LLM 调用

**文件**: `app/providers/company_info_provider.py`

**问题**: `get_company_profile()` 和 `get_peers()` 各自独立调用 `_fetch_wikipedia_summary()` + `synthesize_company_profile()`，导致同一公司的 Wikipedia API 被请求两次、DashScope 被调用两次。

**修复方案**: 缓存同一个 (ticker, company_name) 的 LLM 合成结果，避免重复网络调用。

将 `WebCompanyInfoProvider` 改为：

```python
class WebCompanyInfoProvider(CompanyInfoProvider):
    """Network-backed provider using public web content plus optional DashScope synthesis."""

    def __init__(self, timeout_seconds: float | None = None, user_agent: str | None = None) -> None:
        self.timeout_seconds = timeout_seconds or settings.web_request_timeout_seconds
        self.user_agent = user_agent or settings.web_user_agent
        self.fallback = MockCompanyInfoProvider()
        self.llm = AliyunLLMService()
        self._cache: dict[str, tuple[dict, dict | None]] = {}  # key -> (wiki_summary, llm_profile)

    def _get_or_fetch(self, ticker: str, company_name: str) -> tuple[dict, dict | None]:
        """Fetch wikipedia + LLM profile once per (ticker, company_name)."""
        cache_key = f"{ticker.upper()}|{company_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        summary = self._fetch_wikipedia_summary(company_name)
        llm_profile = None
        if self.llm.available and summary.get("extract"):
            llm_profile = self.llm.synthesize_company_profile(ticker, company_name, {"wikipedia_summary": summary})
        self._cache[cache_key] = (summary, llm_profile)
        return summary, llm_profile

    def get_company_profile(self, ticker: str, company_name: str) -> dict:
        summary, llm_profile = self._get_or_fetch(ticker, company_name)
        profile = {
            "as_of_date": date.today().isoformat(),
            "business_model": summary.get("extract", ""),
            "market_space": summary.get("description", ""),
            # ... 其余字段保持不变 ...
        }
        if llm_profile:
            profile.update({
                "business_model": llm_profile.get("business_model", profile["business_model"]),
                # ... 其余 update 逻辑保持不变 ...
            })
        elif not summary.get("extract"):
            return self.fallback.get_company_profile(ticker, company_name)
        return profile

    def get_peers(self, ticker: str, company_name: str) -> list[dict]:
        _, llm_profile = self._get_or_fetch(ticker, company_name)
        if llm_profile:
            peers = llm_profile.get("peer_list") if isinstance(llm_profile, dict) else None
            # ... 其余 normalize 逻辑保持不变 ...
            if isinstance(peers, list) and peers:
                normalized = [...]  # 保持现有逻辑
                if normalized:
                    return normalized
        return self.fallback.get_peers(ticker, company_name)
```

**效果**: Wikipedia 调用从 2 次降到 1 次，DashScope 调用从 2 次降到 1 次。

---

## 任务 3：修复 MockCompanyInfoProvider 生成无效 Peer Ticker

**文件**: `app/providers/company_info_provider.py`

**问题**: 当 LLM 不可用或 Wikipedia 403 时，`MockCompanyInfoProvider.get_peers()` 对未知 ticker 生成 `{ticker}P1`、`{ticker}P2` 这样的假 ticker（如 `HK.00700P1`）。这些假 ticker 随后被传递给 NewsCollector 和 FinancialAnalyst，导致大量无效 Google News 搜索和本地财报查找失败。

**修复方案**: MockCompanyInfoProvider 应为常见市场的股票提供更合理的默认 peer 映射，且生成的 peer 应标注 `is_placeholder: true`，让下游节点可以跳过。

将 `MockCompanyInfoProvider.get_peers()` 改为：

```python
def get_peers(self, ticker: str, company_name: str) -> list[dict]:
    defaults = {
        "NVDA": [
            {"ticker": "AMD", "company_name": "Advanced Micro Devices", "peer_type": "core", "similarity_score": 0.92},
            {"ticker": "AVGO", "company_name": "Broadcom", "peer_type": "valuation", "similarity_score": 0.83},
        ],
        "US.META": [
            {"ticker": "GOOGL", "company_name": "Alphabet", "peer_type": "core", "similarity_score": 0.88},
            {"ticker": "PINS", "company_name": "Pinterest", "peer_type": "secondary", "similarity_score": 0.61},
        ],
        "HK.00700": [
            {"ticker": "HK.09888", "company_name": "Baidu", "peer_type": "core", "similarity_score": 0.80},
            {"ticker": "HK.09999", "company_name": "NetEase", "peer_type": "secondary", "similarity_score": 0.72},
        ],
        "HK.09988": [
            {"ticker": "HK.09618", "company_name": "JD.com", "peer_type": "core", "similarity_score": 0.85},
            {"ticker": "HK.03690", "company_name": "Meituan", "peer_type": "secondary", "similarity_score": 0.70},
        ],
    }
    peers = defaults.get(ticker.upper())
    if peers:
        return peers
    # 对于没有硬编码映射的 ticker，返回空列表而非生成假 peer
    # 这样下游节点不会浪费时间搜索不存在的公司
    return []
```

**原因**: 返回空列表比返回假 peer 更好——下游节点已对空 peer_list 有容错处理，而假 peer 会触发无效的网络请求和日志噪音。

---

## 任务 4：Wikipedia API 403 修复——增加拼写纠正与备选 slug

**文件**: `app/providers/company_info_provider.py`

**问题**: Wikipedia 对 "Tecent" 返回 403 Forbidden（正确拼写应为 "Tencent"）。Wikipedia REST API 对不存在的页面可能返回 403/404，当前只有一次尝试。

**修复方案**: 添加 Wikipedia search fallback，当直接 slug 失败时，尝试 Wikipedia search API 找到最佳匹配。

在 `WebCompanyInfoProvider` 中修改 `_fetch_wikipedia_summary()`:

```python
def _fetch_wikipedia_summary(self, company_name: str) -> dict:
    """Try direct slug first, then Wikipedia search API as fallback."""
    result = self._try_wikipedia_slug(company_name)
    if result:
        return result
    # 尝试变体：去掉空格、添加常见后缀
    for variant in [f"{company_name} (company)", f"{company_name} Holdings"]:
        result = self._try_wikipedia_slug(variant)
        if result:
            return result
    # 最后 fallback 到 Wikipedia search API
    return self._search_wikipedia(company_name)

def _try_wikipedia_slug(self, name: str) -> dict:
    slug = quote(name.replace(" ", "_"))
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
    headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            if response.status_code in (403, 404):
                return {}
            response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) and payload.get("extract") else {}
    except Exception as exc:
        logger.warning("wikipedia_summary_fetch_failed", name=name, error=str(exc))
        return {}

def _search_wikipedia(self, company_name: str) -> dict:
    """Use Wikipedia's search endpoint to find the best matching page."""
    search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote(company_name)}&srlimit=1&format=json"
    headers = {"User-Agent": self.user_agent}
    try:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(search_url, headers=headers)
            response.raise_for_status()
        data = response.json()
        results = data.get("query", {}).get("search", [])
        if results:
            title = results[0]["title"]
            return self._try_wikipedia_slug(title)
    except Exception as exc:
        logger.warning("wikipedia_search_failed", company_name=company_name, error=str(exc))
    return {}
```

**效果**: 输入 "Tecent" 时，search API 能纠正为 "Tencent" 并返回有效数据。

---

## 任务 5：datetime.utcnow() 弃用警告修复

**文件**: `app/providers/news_provider.py` (第 36、61 行), `app/services/event_extraction_service.py` (第 33、52 行)

**问题**: pytest 输出 `DeprecationWarning: datetime.datetime.utcnow() is deprecated`。Python 3.12+ 将在未来移除此方法。

**修复**: 全局替换 `datetime.utcnow()` 为 `datetime.now(timezone.utc)`。

### news_provider.py

```python
# 第 2 行 import 修改
from datetime import datetime, timedelta, timezone

# 第 36 行
now = datetime.now(timezone.utc)

# 第 61 行 (get_peer_news 中)
now = datetime.now(timezone.utc)
```

### event_extraction_service.py

```python
# 第 1 行 import 修改
from datetime import datetime, timezone

# 第 33 行和第 52 行中的
datetime.utcnow().date().isoformat()
# 改为
datetime.now(timezone.utc).date().isoformat()
```

---

## 任务 6：DashScope LLM 配置只加载一次

**文件**: `app/services/aliyun_llm_service.py`

**问题**: 日志显示 `dashscope_config_loaded` 事件出现两次，说明 `AliyunLLMService` 被实例化了两次（一次在 `WebCompanyInfoProvider.__init__`，一次在 `ReportService.__init__`）。每次实例化都触发配置加载和日志输出。

**修复方案**: 让 `AliyunLLMService` 支持单例或共享实例。

在 `app/services/aliyun_llm_service.py` 底部添加:

```python
_shared_instance: AliyunLLMService | None = None


def get_llm_service() -> AliyunLLMService:
    """Return a shared LLM service instance."""
    global _shared_instance
    if _shared_instance is None:
        _shared_instance = AliyunLLMService()
    return _shared_instance
```

然后修改使用方:

### `app/providers/company_info_provider.py` (第 77 行):
```python
# 原来
self.llm = AliyunLLMService()
# 改为
from app.services.aliyun_llm_service import get_llm_service
self.llm = get_llm_service()
```

### `app/services/report_service.py` (第 17 行):
```python
# 原来
self.llm_service = llm_service or AliyunLLMService()
# 改为
from app.services.aliyun_llm_service import get_llm_service
self.llm_service = llm_service or get_llm_service()
```

---

## 任务 7：Web 前端 XSS 防护强化

**文件**: `app/web/index.html`

**问题**: `renderResult()` 函数（第 453 行）通过 `innerHTML` 注入 `data.ticker`、`data.company_name`、`data.final_rating` 等后端返回数据。虽然 `report_markdown` 通过 `escapeHtml()` 处理了，但 stat-card 中的字段未转义。若后端返回含恶意脚本的公司名称（例如通过 LLM 注入），可导致 XSS。

**修复**: 对所有 `innerHTML` 中插入的后端数据使用 `escapeHtml()`。

修改 `renderResult()` 中的 stat-card 部分:

```javascript
resultRoot.innerHTML = `
  <div class="summary-grid">
    <div class="stat-card">
      <small>股票代码</small>
      <strong>${escapeHtml(data.ticker)}</strong>
    </div>
    <div class="stat-card">
      <small>公司名称</small>
      <strong>${escapeHtml(data.company_name)}</strong>
    </div>
    <div class="stat-card">
      <small>最终评级</small>
      <strong>${escapeHtml(data.final_rating)}</strong>
    </div>
  </div>
  // ... 其余部分
  <div class="report-box">${escapeHtml(summary || "暂无摘要。")}</div>
  // ...
`;
```

同理，`warnings` 展示也要转义:

```javascript
.map((item) => `- ${escapeHtml(String(item))}`)
```

---

## 任务 8：WebNewsProvider 对 Google News 添加 User-Agent 声明最佳实践

**文件**: `app/providers/news_provider.py`

**问题**: Google News RSS 虽然目前正常返回 200，但对高频请求或无明确 User-Agent 的爬虫可能返回 429 或空内容。当前 Accept header 是 `application/rss+xml` 等，User-Agent 是 `investment-mas/0.1`。

**修复建议**: 添加请求间隔控制和响应内容校验。

```python
def _fetch_google_news(self, ticker: str, company_name: str) -> list[dict]:
    query = quote_plus(f'"{company_name}" {ticker} earnings OR guidance OR filing OR investor')
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        text = self._http_get(url)
    except Exception as exc:
        logger.warning("google_news_fetch_failed", ticker=ticker, error=str(exc))
        return []
    if not text.strip() or not text.strip().startswith("<?xml"):
        logger.warning("google_news_empty_or_invalid", ticker=ticker)
        return []
    # ... 其余解析逻辑不变
```

---

## 任务 9：LLM synthesize_report 传入 state 时剥除冗余数据

**文件**: `app/services/report_service.py`

**问题**: `synthesize_report(state)` 将完整 workflow state（含所有新闻全文、raw_source 等大量冗余数据）作为 JSON 发送给 DashScope。这会：
1. 消耗大量 token（增加 API 费用）
2. 可能超过 max_tokens 限制
3. 降低 LLM 分析质量（噪声数据）

**修复**: 在发送前精简 state，只保留 LLM 需要的摘要数据。

在 `ReportService.build()` 中，替换第 86 行:

```python
# 原来
llm_payload = self.llm_service.synthesize_report(state)

# 改为
llm_state = {
    "ticker": state.get("ticker"),
    "company_name": state.get("company_name"),
    "company_profile": {
        k: state.get("company_profile", {}).get(k)
        for k in ("business_model", "market_space", "competition_landscape", "corporate_culture", "revenue_model")
    },
    "peer_list": [
        {"ticker": p.get("ticker"), "company_name": p.get("company_name")}
        for p in state.get("peer_list", [])[:5]
    ],
    "financial_summary": {
        k: state.get("financial_summary", {}).get(k)
        for k in ("years", "revenue_cagr", "latest_metrics", "narrative")
    },
    "peer_comparison": {
        "metrics": state.get("peer_comparison", {}).get("metrics", {}),
        "summary": state.get("peer_comparison", {}).get("summary", []),
    },
    "valuation_result": state.get("valuation_result", {}),
    "risk_assessment": {
        k: state.get("risk_assessment", {}).get(k)
        for k in ("overall_risk_score", "red_flags", "key_risks", "monitoring_signals", "recommendation")
    },
    "event_list": [
        {"event_type": e.get("event_type"), "sentiment": e.get("sentiment"), "event_summary": e.get("event_summary")}
        for e in state.get("event_list", [])[:10]
    ],
    "financial_red_flags": state.get("financial_red_flags", []),
}
llm_payload = self.llm_service.synthesize_report(llm_state)
```

**效果**: 减少 50-80% 的 token 消耗，提升 LLM 响应质量。

---

## 任务 10：AliyunLLMService.chat() 添加重试和超时错误分类

**文件**: `app/services/aliyun_llm_service.py`

**问题**: 当前 `chat()` 方法在 DashScope 返回错误（429 Rate Limit、5xx Server Error、网络超时）时直接抛异常，由节点层 try/except 捕获。但异常信息中没有区分是否可重试。

**修复**: 对可重试错误添加一次重试。

```python
def chat(self, messages: list[dict[str, Any]], temperature: float | None = None) -> str | None:
    """Send a chat-completions request to DashScope with one retry on transient errors."""
    if not self.available:
        return None

    url = f"{self.config.base_url}/chat/completions"
    payload = {
        "model": self.config.model,
        "messages": messages,
        "temperature": temperature if temperature is not None else self.config.temperature,
    }
    if self.config.max_tokens is not None:
        payload["max_tokens"] = self.config.max_tokens
    headers = {
        "Authorization": f"Bearer {self.config.api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
            return body["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                import time
                time.sleep(2)
                continue
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_error = exc
            if attempt == 0:
                import time
                time.sleep(2)
                continue
            raise
    raise last_error  # type: ignore[misc]
```

---

## 任务 11：前端进度轮播改为基于 SSE 的真实进度（可选增强）

**文件**: `app/web/index.html`, `app/api/routes/research.py`

**当前状态**: 前端进度条是纯粹的定时器轮播（每 1.5 秒切换一个 step），与后端实际执行进度无关。

**如果想改为真实进度**（MVP 后的增强，非必需）:
1. 后端改为 SSE（Server-Sent Events）或 WebSocket 推送节点完成事件
2. 前端通过 EventSource 监听并更新对应 step 状态

**MVP 阶段的轻量改进**: 调整轮播间隔为更合理的时间（DashScope 调用约 15-20 秒，总流程约 60-90 秒），将间隔从 1.5 秒改为动态：

```javascript
// 前几步快，后几步慢（模拟真实执行时间分布）
const stepDurations = [8000, 12000, 15000, 20000, 15000, 10000]; // 毫秒

function startProgressTicker() {
    stopProgressTicker();
    activeStepIndex = 0;
    renderProgress(activeStepIndex);
    statusTitle.textContent = `正在执行 ${steps[0][1]}`;
    statusSubtitle.textContent = steps[0][2];

    function advanceStep() {
        activeStepIndex++;
        if (activeStepIndex >= steps.length) {
            activeStepIndex = steps.length - 1; // 停在最后一步
            return;
        }
        renderProgress(activeStepIndex);
        statusTitle.textContent = `正在执行 ${steps[activeStepIndex][1]}`;
        statusSubtitle.textContent = steps[activeStepIndex][2];
        progressTimer = window.setTimeout(advanceStep, stepDurations[activeStepIndex] || 10000);
    }
    progressTimer = window.setTimeout(advanceStep, stepDurations[0]);
}
```

---

## 任务 12：补充 WebCompanyInfoProvider 和 WebNewsProvider 的测试

**新建或扩展**: `tests/test_web_providers.py`

```python
"""Tests for web-backed providers (mocked network calls)."""

from unittest.mock import patch, MagicMock
from app.providers.company_info_provider import WebCompanyInfoProvider
from app.providers.news_provider import WebNewsProvider


class TestWebCompanyInfoProvider:
    def test_falls_back_to_mock_when_wikipedia_403(self):
        """Provider should return mock data when Wikipedia returns 403."""
        provider = WebCompanyInfoProvider()
        with patch.object(provider, "_fetch_wikipedia_summary", return_value={}):
            profile = provider.get_company_profile("HK.00700", "Tencent")
        assert isinstance(profile, dict)
        assert profile.get("business_model")  # should have mock data, not empty

    def test_peers_returns_list_when_llm_unavailable(self):
        """Provider should return mock peers when LLM is not available."""
        provider = WebCompanyInfoProvider()
        provider.llm = MagicMock()
        provider.llm.available = False
        peers = provider.get_peers("HK.00700", "Tencent")
        assert isinstance(peers, list)

    def test_no_duplicate_wikipedia_calls(self):
        """Calling get_company_profile + get_peers should only fetch Wikipedia once."""
        provider = WebCompanyInfoProvider()
        fetch_count = 0
        original_fetch = provider._fetch_wikipedia_summary

        def counting_fetch(name):
            nonlocal fetch_count
            fetch_count += 1
            return {"extract": "Test company", "description": "A tech company"}

        with patch.object(provider, "_fetch_wikipedia_summary", side_effect=counting_fetch):
            provider.llm = MagicMock()
            provider.llm.available = False
            provider.get_company_profile("TEST", "TestCo")
            provider.get_peers("TEST", "TestCo")
        # After Task 2 fix, should be called only once
        assert fetch_count <= 1


class TestWebNewsProvider:
    def test_google_news_parse_empty_xml(self):
        """Provider should handle empty/invalid XML gracefully."""
        provider = WebNewsProvider()
        with patch.object(provider, "_http_get", return_value="not valid xml"):
            result = provider.get_company_news("NVDA", "NVIDIA")
        assert result == []

    def test_sec_filings_skipped_for_hk_ticker(self):
        """SEC filings should not be fetched for HK market tickers."""
        provider = WebNewsProvider()
        with patch.object(provider, "_fetch_google_news", return_value=[]):
            with patch.object(provider, "_fetch_sec_filings") as mock_sec:
                provider.get_company_news("HK.00700", "Tencent")
        mock_sec.assert_not_called()

    def test_dedupe_removes_duplicate_urls(self):
        provider = WebNewsProvider()
        articles = [
            {"url": "https://example.com/a", "title": "Article A", "published_at": "2024-01-01"},
            {"url": "https://example.com/a", "title": "Article A Dup", "published_at": "2024-01-02"},
            {"url": "https://example.com/b", "title": "Article B", "published_at": "2024-01-03"},
        ]
        result = provider._dedupe_and_trim(articles)
        assert len(result) == 2
```

---

## 任务 13：补充 AliyunLLMService 的 JSON 解析边界测试

**扩展**: `tests/test_aliyun_llm_service.py`

```python
def test_extract_json_from_markdown_code_block():
    """LLM sometimes wraps JSON in ```json ... ``` blocks."""
    svc = AliyunLLMService()
    content = '```json\n{"key": "value"}\n```'
    result = svc._extract_json_object(content)
    assert result == {"key": "value"}

def test_extract_json_with_leading_text():
    """LLM sometimes adds explanation before JSON."""
    svc = AliyunLLMService()
    content = 'Here is the result:\n{"key": "value"}'
    result = svc._extract_json_object(content)
    assert result == {"key": "value"}

def test_extract_json_returns_none_for_list():
    """Should return None if LLM returns a JSON array instead of object."""
    svc = AliyunLLMService()
    result = svc._extract_json_object('[1, 2, 3]')
    assert result is None

def test_extract_json_returns_none_for_garbage():
    svc = AliyunLLMService()
    result = svc._extract_json_object("This is not JSON at all.")
    assert result is None
```

注意: `_extract_json_object()` 目前不处理 ```json 代码块包裹的情况。如果上面第一个测试失败，需要在 `_extract_json_object()` 中添加 markdown code fence 剥离：

```python
def _extract_json_object(self, content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    text = content.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # ... 其余逻辑保持不变
```

---

## 任务 14：静态文件服务改进

**文件**: `app/main.py`

**当前状态**: 仅服务 `index.html` 一个文件。如果未来前端需要额外资源（CSS/JS/图片），需要挂载静态文件目录。

```python
from fastapi.staticfiles import StaticFiles

# 在 router 注册之后添加
if WEB_ROOT.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")
```

---

## 执行优先级

| 顺序 | 任务 | 优先级 | 类型 |
|------|------|--------|------|
| 1 | 任务 3: 修复 Mock Peer 生成假 ticker | **P0** | 运行时 bug |
| 2 | 任务 2: Wikipedia + LLM 去重缓存 | **P0** | 性能/成本 |
| 3 | 任务 4: Wikipedia 403 搜索 fallback | **P0** | 运行时 bug |
| 4 | 任务 5: datetime.utcnow 弃用修复 | **P1** | 警告消除 |
| 5 | 任务 6: LLM 配置单例 | **P1** | 日志/性能 |
| 6 | 任务 9: LLM state 精简 | **P1** | Token 成本 |
| 7 | 任务 7: XSS 防护 | **P1** | 安全 |
| 8 | 任务 1: favicon 404 | **P2** | 日志整洁 |
| 9 | 任务 10: LLM 重试 | **P2** | 可靠性 |
| 10 | 任务 8: Google News 响应校验 | **P2** | 鲁棒性 |
| 11 | 任务 12: Web Provider 测试 | **P1** | 测试覆盖 |
| 12 | 任务 13: LLM JSON 解析测试 | **P1** | 测试覆盖 |
| 13 | 任务 11: 进度条时间优化 | **P2** | UX |
| 14 | 任务 14: 静态文件挂载 | **P2** | 扩展性 |

---

## 约束与注意事项

1. 所有修改需保持 41 个现有测试通过
2. WebCompanyInfoProvider 和 WebNewsProvider 的测试应 mock 网络调用，不依赖真实网络
3. `get_llm_service()` 单例需保证测试中可替换（接受参数覆盖）
4. Wikipedia search API 有请求限制，需设合理 timeout
5. 前端 XSS 修复必须覆盖所有 `innerHTML` 中的动态内容
6. `_extract_json_object()` 的 markdown fence 剥离要兼容 `json`、`JSON`、无语言标记三种情况
7. Mock peer 列表变更后，需同步检查 `tests/helpers.py` 和 `tests/test_workflow.py` 中的测试数据是否受影响
