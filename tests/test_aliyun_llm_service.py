"""DashScope / Qwen service configuration tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.core.config import get_settings
from app.schemas.llm import DashScopeLLMConfig
from app.services.aliyun_llm_service import AliyunLLMService, reset_llm_service


def test_aliyun_llm_service_can_load_mas_style_config(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "llm_config.json"
    config_path.write_text(
        json.dumps(
            {
                "name": "百炼大模型",
                "provider": "阿里",
                "base_url": "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
                "api_key": "test-key-from-json",
                "model": "qwen-plus",
                "temperature": 0.35,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("INVESTMENT_MAS_DASHSCOPE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("INVESTMENT_MAS_DASHSCOPE_API_KEY", "test-key-from-env")
    get_settings.cache_clear()
    reset_llm_service()

    service = AliyunLLMService()

    assert service.available is True
    assert service.config.base_url.endswith("/compatible-mode/v1")
    assert service.config.model == "auto"
    assert service.config.api_key == "test-key-from-env"
    assert service.config.temperature == 0.2

    reset_llm_service()
    get_settings.cache_clear()


def test_extract_json_from_markdown_code_block() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    result = service._extract_json_object('```json\n{"key":"value"}\n```')
    assert result == {"key": "value"}


def test_extract_json_with_leading_text() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    result = service._extract_json_object('Here is the result:\n{"key":"value"}')
    assert result == {"key": "value"}


def test_extract_json_returns_none_for_list() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    result = service._extract_json_object("[1,2,3]")
    assert result is None


def test_extract_json_returns_none_for_garbage() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    result = service._extract_json_object("This is not JSON at all.")
    assert result is None


def test_settings_auto_discover_dashscope_config_from_sibling_mas(tmp_path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspace"
    project_root = workspace_root / "investment-mas"
    llm_dir = workspace_root / "MAS" / "data" / "llm_configs"
    llm_dir.mkdir(parents=True)

    old_file = llm_dir / "llm_old.json"
    old_file.write_text(
        json.dumps({"api_key": "x", "base_url": "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"}),
        encoding="utf-8",
    )

    config_file = llm_dir / "llm_live.json"
    config_file.write_text(
        json.dumps(
            {
                "api_key": "real-key",
                "base_url": "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
                "model": "qwen-plus",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.core.config.PROJECT_ROOT", Path(project_root))
    monkeypatch.setenv("INVESTMENT_MAS_DASHSCOPE_CONFIG_PATH", "")
    monkeypatch.delenv("INVESTMENT_MAS_DASHSCOPE_API_KEY", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.dashscope_config_path == str(config_file)

    get_settings.cache_clear()
    reset_llm_service()


def test_synthesize_company_profile_returns_none_when_schema_invalid() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    service.chat = MagicMock(return_value=json.dumps({"ticker": "NVDA", "company_name": "英伟达"}))

    result = service.synthesize_company_profile("NVDA", "英伟达", {"wikipedia_summary": {"extract": "GPU 公司"}})

    assert result is None


def test_annotate_peer_candidates_returns_validated_dicts() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    service.chat = MagicMock(
        return_value=json.dumps(
            {
                "company_name": "腾讯",
                "ticker": "HK.00700",
                "peer_analysis": [
                    {
                        "ticker": "HK.09888",
                        "company_name": "百度",
                        "peer_type": "core",
                        "similarity_dimensions": ["industry", "business_model"],
                        "rationale": "同属互联网平台，商业模式存在可比性。",
                        "confidence": 0.8,
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    result = service.annotate_peer_candidates("HK.00700", "腾讯", [{"ticker": "HK.09888"}], {"summary": "..."})

    assert result is not None
    assert result[0]["peer_type"] == "core"
    assert result[0]["similarity_dimensions"] == ["industry", "business_model"]


def test_analyze_news_articles_returns_none_when_event_type_invalid() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    service.chat = MagicMock(
        return_value=json.dumps(
            {
                "company_name": "腾讯",
                "ticker": "HK.00700",
                "news_analysis": [
                    {
                        "news_id": "1",
                        "title": "测试",
                        "summary_cn": "摘要",
                        "events": [
                            {
                                "event_type": "not_allowed",
                                "sentiment": "neutral",
                                "importance_score": 0.3,
                                "confidence_score": 0.6,
                                "risk_tags": [],
                                "impact_dimensions": [],
                                "event_summary": "事件",
                                "evidence_span": "测试",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    result = service.analyze_news_articles("HK.00700", "腾讯", [{"title": "测试"}])

    assert result is None


def test_analyze_news_articles_accepts_summary_alias() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    service.chat = MagicMock(
        return_value=json.dumps(
            {
                "company_name": "恒玄科技",
                "ticker": "SH.688608",
                "news_analysis": [
                    {
                        "news_id": "1",
                        "title": "恒玄科技营收结构进一步多元化",
                        "summary": "恒玄科技营收结构持续多元化，但仍需结合年报验证收入来源变化。",
                        "events": [
                            {
                                "event_type": "other",
                                "sentiment": "positive",
                                "importance_score": 0.65,
                                "confidence_score": 0.7,
                                "risk_tags": ["growth"],
                                "impact_dimensions": ["revenue"],
                                "event_summary": "营收结构多元化可能提升收入韧性。",
                                "evidence_span": "营收结构进一步多元化",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )

    result = service.analyze_news_articles("SH.688608", "恒玄科技", [{"title": "测试"}])

    assert result is not None
    assert result[0]["summary_cn"] == "恒玄科技营收结构持续多元化，但仍需结合年报验证收入来源变化。"


def test_synthesize_report_returns_validated_payload() -> None:
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test"))
    service.chat = MagicMock(
        return_value=json.dumps(
            {
                "company_name": "腾讯",
                "ticker": "HK.00700",
                "executive_summary": "结论谨慎偏中性。",
                "business_analysis": {
                    "core_judgement": "商业模式成熟。",
                    "supporting_facts": ["平台生态稳定。"],
                    "open_questions": ["广告恢复节奏仍需跟踪。"],
                },
                "financial_analysis": {
                    "core_judgement": "财务表现稳健。",
                    "supporting_facts": ["现金流较稳定。"],
                    "red_flags": [],
                    "open_questions": [],
                },
                "peer_comparison_analysis": {
                    "core_judgement": "相对同行仍具优势。",
                    "strengths_vs_peers": ["流量基础较强。"],
                    "weaknesses_vs_peers": ["部分业务增速偏慢。"],
                    "open_questions": [],
                },
                "valuation_analysis": {
                    "core_judgement": "估值处于可观察区间。",
                    "preferred_method": "mixed",
                    "bull_base_bear_summary": "三情景差异较大。",
                    "key_assumptions": ["利润率维持稳定。"],
                    "valuation_risks": ["宏观波动。"],
                },
                "risk_review": {
                    "core_judgement": "主要风险在监管和增长不确定性。",
                    "key_risks": ["监管变化。"],
                    "monitoring_signals": ["广告恢复。"],
                    "what_would_change_my_mind": ["新业务盈利改善。"],
                },
                "final_recommendation": {
                    "final_rating": "watch",
                    "reason": "质量尚可，但仍需观察。",
                    "confidence": 0.72,
                },
            },
            ensure_ascii=False,
        )
    )

    result = service.synthesize_report({"ticker": "HK.00700", "company_name": "腾讯"})

    assert result is not None
    assert result["final_recommendation"]["final_rating"] == "watch"


def _capture_chat_payload(service: AliyunLLMService, monkeypatch, **chat_kwargs) -> dict:
    """Run service.chat() against a stubbed httpx client and return the sent payload."""

    captured: dict[str, object] = {}

    class _StubResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    class _StubClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = json or {}
            return _StubResponse()

    monkeypatch.setattr("app.services.aliyun_llm_service.httpx.Client", _StubClient)
    service.chat([{"role": "user", "content": "hi"}], **chat_kwargs)
    return captured["payload"]


def test_auto_model_omits_temperature_param(monkeypatch) -> None:
    """The token-plan "auto" router HTTP-400s on `temperature`; it must be omitted."""

    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test", model="auto", temperature=0.2))

    payload = _capture_chat_payload(service, monkeypatch)

    assert payload["model"] == "auto"
    assert "temperature" not in payload, "auto rejects temperature — sending it caused HTTP 400"


def test_named_models_still_send_temperature(monkeypatch) -> None:
    """Non-auto models accept temperature and must keep receiving it."""

    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test", model="qwen3.8-max", temperature=0.2))

    payload = _capture_chat_payload(service, monkeypatch)

    assert payload["model"] == "qwen3.8-max"
    assert payload["temperature"] == 0.2


def test_explicit_auto_model_override_omits_temperature(monkeypatch) -> None:
    """Passing model="auto" per-call also drops temperature even on a named default."""

    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test", model="glm-5.3", temperature=0.2))

    payload = _capture_chat_payload(service, monkeypatch, model="auto", temperature=0.15)

    assert payload["model"] == "auto"
    assert "temperature" not in payload


def test_gateway_models_all_route_to_configured_base_url(monkeypatch) -> None:
    """Every catalogued model — deepseek-* included — must stay on token-plan."""

    monkeypatch.setattr(
        "app.services.aliyun_llm_service.get_settings",
        lambda: get_settings().model_copy(update={"enable_deepseek_direct_routing": False}),
    )
    gateway = DashScopeLLMConfig(api_key="test").base_url.rstrip("/")
    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test", model="auto"))

    for model in [
        "auto", "qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "glm-5.3",
        "deepseek-v4-pro", "deepseek-v4.1-flash",
    ]:
        base_url, api_key = service._resolve_provider(model)
        assert base_url == gateway, f"{model} leaked out of the gateway -> {base_url}"
        assert api_key == "test", f"{model} used the wrong credential"


def test_deepseek_direct_routing_is_opt_in(monkeypatch) -> None:
    """deepseek-* only leaves the gateway when the flag is explicitly enabled."""

    service = AliyunLLMService(config=DashScopeLLMConfig(api_key="test", model="auto"))
    monkeypatch.setattr(
        "app.services.aliyun_llm_service.get_settings",
        lambda: get_settings().model_copy(
            update={
                "enable_deepseek_direct_routing": True,
                "deepseek_api_key": "dk-native",
                "deepseek_base_url": "https://api.deepseek.com",
            }
        ),
    )

    base_url, api_key = service._resolve_provider("deepseek-chat")

    assert base_url == "https://api.deepseek.com"
    assert api_key == "dk-native"


