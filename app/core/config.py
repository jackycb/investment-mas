"""Application configuration."""

from functools import lru_cache
import getpass
import json
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"


def _default_database_url() -> str:
    """Build a sane local PostgreSQL default.

    Homebrew PostgreSQL on macOS commonly creates a superuser matching the
    current OS account rather than a `postgres` role.
    """

    local_user = getpass.getuser()
    return f"postgresql+psycopg://{local_user}@localhost:5432/investment_mas"


def _default_dashscope_config_path() -> str | None:
    """Auto-discover a reusable DashScope config from the sibling MAS project."""

    candidate_dir = PROJECT_ROOT.parent / "MAS" / "data" / "llm_configs"
    if not candidate_dir.exists():
        return None

    valid_files: list[Path] = []
    for path in sorted(candidate_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        api_key = str(payload.get("api_key", "")).strip()
        base_url = str(payload.get("base_url", "")).strip()
        if api_key and api_key.lower() != "x" and base_url:
            valid_files.append(path)
    if not valid_files:
        return None
    latest = max(valid_files, key=lambda item: item.stat().st_mtime)
    return str(latest)


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="INVESTMENT_MAS_", extra="ignore")

    app_name: str = "investment-mas"
    app_env: str = "dev"
    debug: bool = True
    api_prefix: str = ""
    database_url: str = Field(default_factory=_default_database_url)
    log_level: str = "INFO"
    report_currency: str = "USD"
    enable_mock_providers: bool = True
    financial_data_provider: str = "hybrid"
    local_financial_base_dir: str = str(DATA_ROOT / "finance_report_web")
    enable_futu_download_fallback: bool = True
    futu_downloader_script_path: str = str(PROJECT_ROOT / "scripts" / "download_financial_data.py")
    futu_download_timeout_seconds: int = 300
    news_provider: str = "web"
    company_info_provider: str = "web"
    dashscope_api_key: str | None = None
    dashscope_config_path: str | None = Field(default_factory=_default_dashscope_config_path)
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-plus"
    dashscope_temperature: float = 0.2
    dashscope_max_tokens: int | None = None
    dashscope_timeout_seconds: float = 120.0
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout_seconds: float = 120.0
    web_request_timeout_seconds: float = 20.0
    web_user_agent: str = "investment-mas/0.1 (research bot; contact: local-dev)"
    news_fetch_limit: int = 8
    enable_llm_report: bool = True
    enable_supplemental_valuation: bool = True
    supplemental_valuation_script_path: str = str(PROJECT_ROOT / "scripts" / "run_value_analysis_by_code.py")
    supplemental_valuation_helper_path: str = str(PROJECT_ROOT / "scripts" / "value_investment_market_value.py")
    supplemental_valuation_workbook: str = str(DATA_ROOT / "value_analysis_by_code.xlsx")
    supplemental_fx_workbook: str = str(DATA_ROOT / "Value_Investment.xlsx")
    enable_legacy_financial_metric_enrichment: bool = True
    legacy_financial_plot_enabled: bool = False
    legacy_financial_parameter_dir: str = str(DATA_ROOT / "parameters")
    company_document_storage_dir: str = str(DATA_ROOT / "company_documents")
    company_profile_index_dir: str = str(DATA_ROOT / "company_profile_index")
    company_profile_vector_backend: str = "chroma"
    company_profile_max_documents: int = 8
    company_profile_chunk_size: int = 1600
    company_profile_chunk_overlap: int = 200
    document_storage_dir: str = str(DATA_ROOT / "company_docs")
    vector_store_type: str = "chroma"
    vector_store_dir: str = str(DATA_ROOT / "vector_store")
    default_embedding_provider: str = "local_hash"
    enable_filing_ingestion: bool = True
    enable_market_us: bool = True
    enable_market_hk: bool = True
    enable_market_cn: bool = True
    default_filing_lookback_years: int = 5
    default_chunk_size: int = 1200
    default_chunk_overlap: int = 180

    @field_validator("dashscope_api_key", "dashscope_config_path", "deepseek_api_key", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: str | None) -> str | None:
        """Treat blank env values as unset."""

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("dashscope_max_tokens", mode="before")
    @classmethod
    def _empty_string_to_none_int(cls, value: int | str | None) -> int | None:
        """Treat blank env values as None for integer fields."""

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return int(stripped)
        return value

    @model_validator(mode="after")
    def _fill_dashscope_config_path(self) -> "Settings":
        """Backfill auto-discovered LLM config when env leaves the path unset."""

        if not self.dashscope_config_path:
            self.dashscope_config_path = _default_dashscope_config_path()
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings."""

    return Settings()
