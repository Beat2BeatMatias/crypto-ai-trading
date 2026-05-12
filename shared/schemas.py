from __future__ import annotations
from enum import Enum
from typing import Annotated, Any
from pydantic import BaseModel, Field, field_validator, model_validator


class DecisorAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class DecisorOutput(BaseModel):
    regime: MarketRegime
    confluences: list[str] = Field(default_factory=list, max_length=10)
    action: DecisorAction
    confidence_base: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    confidence_adjustment: Annotated[float, Field(ge=-0.20, le=0.20)] = 0.0
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    stop_loss: float | None
    take_profit: float | None
    position_size_pct: Annotated[float, Field(ge=0.0, le=0.25)]
    expected_holding_min: Annotated[int, Field(ge=1)] = 1
    reasoning: Annotated[str, Field(max_length=800)]

    @field_validator("position_size_pct", mode="before")
    @classmethod
    def _coerce_null_position_size(cls, v: Any) -> float:
        # LLMs return null for HOLD/SELL decisions; treat as 0.0
        return 0.0 if v is None else v

    @field_validator("expected_holding_min", mode="before")
    @classmethod
    def _coerce_null_holding(cls, v: Any) -> int:
        return 1 if v is None else v

    @field_validator("reasoning", mode="before")
    @classmethod
    def _truncate_reasoning(cls, v: Any) -> Any:
        if isinstance(v, str) and len(v) > 800:
            return v[:797] + "..."
        return v

    @model_validator(mode="after")
    def _buy_requires_sl_and_tp(self) -> "DecisorOutput":
        if self.action == DecisorAction.BUY:
            if self.stop_loss is None:
                raise ValueError("stop_loss is required when action=BUY")
            if self.take_profit is None:
                raise ValueError("take_profit is required when action=BUY")
        return self


class TradeOutcome(BaseModel):
    pnl_usdt: float
    pnl_pct: float
    close_reason: str
    duration_min: int
    fees_usdt: float | None = None
