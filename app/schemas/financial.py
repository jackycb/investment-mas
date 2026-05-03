"""Financial schemas."""

from pydantic import BaseModel, Field


class FinancialMetricPoint(BaseModel):
    """Time-series point for a financial metric."""

    fiscal_year: int
    value: float | None


class FinancialSummarySchema(BaseModel):
    """Aggregated financial summary."""

    years: list[int] = Field(default_factory=list)
    revenue_cagr: float | None = None
    gross_margin_trend: list[FinancialMetricPoint] = Field(default_factory=list)
    net_margin_trend: list[FinancialMetricPoint] = Field(default_factory=list)
    roe_trend: list[FinancialMetricPoint] = Field(default_factory=list)
    roic_trend: list[FinancialMetricPoint] = Field(default_factory=list)
    ocf_vs_net_income: list[dict[str, float | int | None]] = Field(default_factory=list)
    latest_metrics: dict[str, float | None] = Field(default_factory=dict)
    narrative: list[str] = Field(default_factory=list)
