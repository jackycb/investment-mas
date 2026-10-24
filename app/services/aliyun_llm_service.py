"""Alibaba Cloud DashScope / Qwen integration service."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.llm import DashScopeLLMConfig
from app.schemas.llm_output import (
    CompanyProfileLLMOutput,
    InvestmentReportLLMOutput,
    NewsAnalysisLLMOutput,
    PeerExplanationLLMOutput,
)
from app.services.llm_prompt_templates import (
    build_company_profile_messages,
    build_investment_report_messages,
    build_news_analysis_messages,
    build_peer_explanation_messages,
)
from app.services.performance_tracker import STATUS_RETRYING, record_llm_call, record_retry

logger = get_logger(__name__)


class AliyunLLMService:
    """Multi-provider LLM service supporting Alibaba DashScope and DeepSeek.

    All models go through the configured gateway (``dashscope_base_url``),
    which serves the Qwen, DeepSeek and GLM catalogue alike. Only when
    ``enable_deepseek_direct_routing`` is turned on are ``deepseek-*`` models
    sent to DeepSeek's own API instead.
    The service can load configuration directly from local environment
    variables or from a MAS-style ``llm_config`` JSON file.
    """

    # Model prefixes that route to the DeepSeek provider
    _DEEPSEEK_PREFIX = "deepseek-"

    # Models that reject the ``temperature`` request parameter outright.
    # The token-plan ``auto`` router picks the concrete model server-side and
    # answers HTTP 400 ("auto does not support parameter(s): temperature")
    # whenever temperature is sent, so it must be omitted for these models.
    _NO_TEMPERATURE_MODELS = frozenset({"auto"})

    def __init__(
        self,
        config: DashScopeLLMConfig | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        resolved = config or self._load_default_config()
        overrides: dict[str, Any] = {}
        if api_key is not None:
            overrides["api_key"] = api_key
        if base_url is not None:
            overrides["base_url"] = base_url
        if model is not None:
            overrides["model"] = model
        if temperature is not None:
            overrides["temperature"] = temperature
        if max_tokens is not None:
            overrides["max_tokens"] = max_tokens
        if timeout_seconds is not None:
            overrides["timeout_seconds"] = timeout_seconds
        self.config = resolved.model_copy(update=overrides)
        self.config.base_url = self.config.base_url.rstrip("/")
        # Populated by chat() from the provider's `usage` block. Telemetry only.
        self.last_usage: dict[str, Any] = {}

    @classmethod
    def from_config_file(cls, path: str | Path) -> "AliyunLLMService":
        """Build the service from a MAS-style llm_config JSON file."""

        config = DashScopeLLMConfig.from_json_file(path).merge_env_overrides()
        return cls(config=config)

    @property
    def available(self) -> bool:
        """Whether any configured provider credentials are available."""

        if bool(self.config.api_key):
            return True
        settings = get_settings()
        return bool(settings.deepseek_api_key)

    def _resolve_provider(self, model: str | None) -> tuple[str, str]:
        """Return (base_url, api_key) for the provider that should handle *model*.

        Models whose name starts with ``deepseek-`` are routed to DeepSeek;
        everything else uses the default DashScope configuration.
        """

        settings = get_settings()
        effective_model = model or self.config.model
        if (
            settings.enable_deepseek_direct_routing
            and effective_model.startswith(self._DEEPSEEK_PREFIX)
        ):
            deepseek_key = settings.deepseek_api_key
            if not deepseek_key:
                raise RuntimeError(
                    "DeepSeek API key is not configured. "
                    "Set INVESTMENT_MAS_DEEPSEEK_API_KEY in your .env file."
                )
            deepseek_url = settings.deepseek_base_url.rstrip("/")
            logger.info("llm_provider_routing", provider="deepseek", model=effective_model)
            return deepseek_url, deepseek_key
        logger.info("llm_provider_routing", provider="dashscope", model=effective_model)
        return self.config.base_url, self.config.api_key

    def chat(self, messages: list[dict[str, Any]], temperature: float | None = None, model: str | None = None) -> str | None:
        """Send a chat-completions request with light retry logic.

        Automatically routes to the correct provider based on the model name.
        """

        base_url, api_key = self._resolve_provider(model)
        if not api_key:
            return None

        effective_model = model or self.config.model
        # Native DeepSeek calls can be slower — use a generous timeout for them.
        if (
            get_settings().enable_deepseek_direct_routing
            and effective_model.startswith(self._DEEPSEEK_PREFIX)
        ):
            timeout = get_settings().deepseek_timeout_seconds
        else:
            timeout = self.config.timeout_seconds

        url = f"{base_url}/chat/completions"
        payload = {
            "model": effective_model,
            "messages": messages,
        }
        # Some models (e.g. the "auto" router) reject `temperature` entirely.
        if effective_model not in self._NO_TEMPERATURE_MODELS:
            payload["temperature"] = temperature if temperature is not None else self.config.temperature
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(2):
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    body = response.json()
                content = body["choices"][0]["message"]["content"]
                # Token accounting is additive telemetry only: the returned
                # value and the retry semantics are unchanged.
                usage = body.get("usage") or {}
                self.last_usage = dict(usage)
                record_llm_call(
                    model=effective_model,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                )
                return content
            except httpx.HTTPStatusError as exc:
                last_error = exc
                self._record_llm_failure(effective_model, started, exc)
                if exc.response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    logger.warning("llm_chat_retry", attempt=attempt + 1, status_code=exc.response.status_code, model=effective_model)
                    record_retry()
                    time.sleep(2)
                    continue
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_error = exc
                self._record_llm_failure(effective_model, started, exc)
                if attempt == 0:
                    logger.warning("llm_chat_retry", attempt=attempt + 1, error=str(exc), model=effective_model)
                    record_retry()
                    time.sleep(2)
                    continue
                raise
        if last_error:
            raise last_error
        return None

    def _record_llm_failure(self, model: str, started: float, exc: Exception) -> None:
        """Record a failed LLM round-trip without altering control flow."""

        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        record_llm_call(
            model=model,
            duration_ms=(time.perf_counter() - started) * 1000,
            status=STATUS_RETRYING,
            error_type=f"{type(exc).__name__}:{status_code}" if status_code else type(exc).__name__,
        )

    def chat_json(self, messages: list[dict[str, Any]], temperature: float | None = None, model: str | None = None) -> dict[str, Any] | None:
        """Send a chat request and parse the response as a JSON object."""

        content = self.chat(messages, temperature=temperature or 0.15, model=model)
        return self._extract_json_object(content)

    def synthesize_company_profile(self, ticker: str, company_name: str, raw_context: dict[str, Any], model: str | None = None) -> dict[str, Any] | None:
        """Turn scraped network context into a structured company profile."""

        content = self.chat(build_company_profile_messages(company_name, ticker, raw_context), model=model)
        payload = self._extract_json_object(content)
        validated = self._validate_output(payload, CompanyProfileLLMOutput, "company_profile")
        return validated.model_dump() if validated else None

    def annotate_peer_candidates(
        self,
        ticker: str,
        company_name: str,
        candidates: list[dict[str, Any]],
        raw_context: dict[str, Any],
        model: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """Refine rationale for pre-retrieved peer candidates without generating new peers."""

        if not candidates:
            return []
        content = self.chat(
            build_peer_explanation_messages(
                company_name=company_name,
                ticker=ticker,
                company_profile=raw_context,
                peer_candidates=candidates,
            ),
            temperature=0.1,
            model=model,
        )
        payload = self._extract_json_object(content)
        validated = self._validate_output(payload, PeerExplanationLLMOutput, "peer_explanation")
        peers = [item.model_dump() for item in validated.peer_analysis] if validated else None
        return peers if isinstance(peers, list) else None

    def analyze_news_articles(
        self,
        ticker: str,
        company_name: str,
        articles: list[dict[str, Any]],
        model: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """Summarize already-fetched articles and extract structured events."""

        if not articles:
            return []
        compact_articles = [
            {
                "news_id": item.get("url") or item.get("id") or str(index),
                "ticker": item.get("ticker"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "content": item.get("content"),
                "published_at": item.get("published_at"),
                "source_name": item.get("source_name"),
            }
            for index, item in enumerate(articles[:8], start=1)
        ]
        content = self.chat(
            build_news_analysis_messages(company_name=company_name, ticker=ticker, news_list=compact_articles),
            temperature=0.15,
            model=model,
        )
        payload = self._extract_json_object(content)
        validated = self._validate_output(payload, NewsAnalysisLLMOutput, "news_analysis")
        result = [item.model_dump() for item in validated.news_analysis] if validated else None
        return result if isinstance(result, list) else None

    def synthesize_report(self, state: dict[str, Any], model: str | None = None) -> dict[str, Any] | None:
        """Generate a richer report narrative and rating from the assembled state."""

        content = self.chat(
            build_investment_report_messages(
                company_name=str(state.get("company_name", "")),
                ticker=str(state.get("ticker", "")),
                company_profile=state.get("company_profile", {}),
                business_analysis=state.get("business_analysis", {}),
                segment_analysis=state.get("segment_analysis", {}),
                culture_analysis=state.get("culture_analysis", {}),
                filing_coverage=state.get("filing_coverage", {}),
                financial_summary=state.get("financial_summary", {}),
                peer_comparison=state.get("peer_comparison", {}),
                financial_red_flags=state.get("financial_red_flags", []),
                valuation_result=state.get("valuation_result", {}),
                valuation_assumptions=state.get("valuation_assumptions", {}),
                risk_assessment=state.get("risk_assessment", {}),
                event_list=state.get("event_list", []),
                company_news=state.get("company_news"),
            ),
            temperature=0.15,
            model=model,
        )
        payload = self._extract_json_object(content)
        validated = self._validate_output(payload, InvestmentReportLLMOutput, "investment_report")
        return validated.model_dump() if validated else None

    def _extract_json_object(self, content: str | None) -> dict[str, Any] | None:
        """Try to parse a JSON object from model output."""

        if not content:
            return None
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.warning("dashscope_json_parse_failed", content_preview=text[:300])
            return None
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            logger.warning("dashscope_json_parse_failed", content_preview=text[:300])
            return None

    def _validate_output(
        self,
        payload: dict[str, Any] | None,
        schema: type[BaseModel],
        payload_type: str,
    ) -> BaseModel | None:
        """Validate parsed LLM JSON against the expected Pydantic schema."""

        if not isinstance(payload, dict):
            return None
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "dashscope_output_validation_failed",
                payload_type=payload_type,
                errors=exc.errors(include_url=False),
                payload_preview=json.dumps(payload, ensure_ascii=False)[:600],
            )
            return None

    def _load_default_config(self) -> DashScopeLLMConfig:
        """Load config from settings or a MAS-style config file path."""

        settings = get_settings()
        config_path = settings.dashscope_config_path
        if config_path:
            try:
                config = DashScopeLLMConfig.from_json_file(config_path).merge_env_overrides()
                logger.info("dashscope_config_loaded", config_path=str(config_path), config=config.redacted)
                return config
            except Exception as exc:
                logger.warning("dashscope_config_load_failed", config_path=str(config_path), error=str(exc))
        config = DashScopeLLMConfig.from_settings()
        logger.info("dashscope_config_loaded", source="settings", config=config.redacted)
        return config


_shared_instance: AliyunLLMService | None = None


def get_llm_service() -> AliyunLLMService:
    """Return a process-wide shared LLM service instance."""

    global _shared_instance
    if _shared_instance is None:
        _shared_instance = AliyunLLMService()
    return _shared_instance


def reset_llm_service() -> None:
    """Reset the shared LLM service for tests."""

    global _shared_instance
    _shared_instance = None
