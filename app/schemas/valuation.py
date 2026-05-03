"""Valuation schemas."""

from pydantic import BaseModel, Field


class ScenarioValuation(BaseModel):
    """Scenario valuation values."""

    bull: float
    base: float
    bear: float


class ValuationResultSchema(BaseModel):
    """Unified valuation result."""

    value_investing: ScenarioValuation
    growth: ScenarioValuation
    blended: ScenarioValuation
    commentary: list[str] = Field(default_factory=list)
