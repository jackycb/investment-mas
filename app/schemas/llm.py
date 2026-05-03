"""LLM configuration schemas."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.config import get_settings


class DashScopeLLMConfig(BaseModel):
    """Configuration for DashScope OpenAI-compatible chat models."""

    name: str = "DashScope"
    provider: str = "openai-compatible"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen-plus"
    temperature: float = 0.2
    timeout_seconds: float = 120.0
    max_tokens: int | None = None

    @classmethod
    def from_settings(cls) -> "DashScopeLLMConfig":
        """Build config from local application settings."""

        settings = get_settings()
        return cls(
            name="DashScope",
            provider="aliyun",
            base_url=settings.dashscope_base_url,
            api_key=settings.dashscope_api_key or "",
            model=settings.dashscope_model,
            temperature=settings.dashscope_temperature,
            timeout_seconds=settings.dashscope_timeout_seconds,
            max_tokens=settings.dashscope_max_tokens,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "DashScopeLLMConfig":
        """Load config from a MAS-style llm_config JSON file."""

        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            name=str(payload.get("name", "DashScope")),
            provider=str(payload.get("provider", "openai-compatible")),
            base_url=str(payload.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")),
            api_key=str(payload.get("api_key", "")),
            model=str(payload.get("model", "qwen-plus")),
            temperature=float(payload.get("temperature", 0.2)),
            timeout_seconds=float(payload.get("timeout_seconds", get_settings().dashscope_timeout_seconds)),
            max_tokens=payload.get("max_tokens"),
        )

    def merge_env_overrides(self) -> "DashScopeLLMConfig":
        """Prefer explicit local environment values when they are set."""

        settings = get_settings()
        updates: dict[str, object] = {}
        if settings.dashscope_api_key:
            updates["api_key"] = settings.dashscope_api_key
        if settings.dashscope_base_url:
            updates["base_url"] = settings.dashscope_base_url
        if settings.dashscope_model:
            updates["model"] = settings.dashscope_model
        updates["temperature"] = settings.dashscope_temperature
        updates["timeout_seconds"] = settings.dashscope_timeout_seconds
        updates["max_tokens"] = settings.dashscope_max_tokens
        return self.model_copy(update=updates)

    @property
    def redacted(self) -> dict[str, object]:
        """Return a logging-safe view of the config."""

        return {
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "model": self.model,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
        }
