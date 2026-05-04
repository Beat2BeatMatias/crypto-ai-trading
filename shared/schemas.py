from __future__ import annotations
from enum import Enum
from typing import Annotated
from pydantic import BaseModel, Field, model_validator


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
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    stop_loss: float | None
    take_profit: float | None
    position_size_pct: Annotated[float, Field(ge=0.0, le=0.25)]
    reasoning: Annotated[str, Field(max_length=240)]

    @model_validator(mode="after")
    def _buy_requires_stop_loss(self) -> "DecisorOutput":
        if self.action == DecisorAction.BUY and self.stop_loss is None:
            raise ValueError("stop_loss is required when action=BUY")
        return self


class TradeOutcome(BaseModel):
    pnl_usdt: float
    pnl_pct: float
    close_reason: str
    duration_min: int
    fees_usdt: float | None = None
