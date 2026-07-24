from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dyadtetrade.modules.portfolio import (
    PortfolioConstraints,
    allocate,
    create_rebalance_plan,
    generate_order_intents,
)

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


class PortfolioConstraintsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_single_weight: float = Field(default=0.2, gt=0, le=1)
    max_total_weight: float = Field(default=0.95, gt=0, le=1)
    cash_buffer: float = Field(default=0.05, ge=0, lt=1)
    min_trade_value: float = Field(default=1000, ge=0)
    min_rebalance_weight: float = Field(default=0.005, ge=0, le=1)
    turnover_limit: float = Field(default=1.0, ge=0, le=2)
    industry_limits: dict[str, float] = Field(default_factory=dict)
    risk_budgets: dict[str, float] = Field(default_factory=dict)

    def to_domain(self) -> PortfolioConstraints:
        return PortfolioConstraints(**self.model_dump())


class AllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    returns: dict[str, list[float]]
    method: Literal[
        "equal",
        "market_cap",
        "inverse_volatility",
        "risk_parity",
        "minimum_variance",
        "mean_variance",
        "maximum_diversification",
    ] = "equal"
    market_caps: dict[str, float] | None = None
    current_weights: dict[str, float] | None = None
    industries: dict[str, str] | None = None
    constraints: PortfolioConstraintsRequest = Field(
        default_factory=lambda: PortfolioConstraintsRequest()
    )


class OrderIntentRequest(BaseModel):
    target_weights: dict[str, float]
    current_values: dict[str, float] = Field(default_factory=dict)
    prices: dict[str, float]
    total_assets: float = Field(gt=0)
    lot_sizes: dict[str, int] | None = None
    constraints: PortfolioConstraintsRequest = Field(
        default_factory=lambda: PortfolioConstraintsRequest()
    )


class RebalancePlanRequest(OrderIntentRequest):
    effective_at: datetime
    industries: dict[str, str] | None = None


@router.post("/allocate")
def portfolio_allocation(request: AllocationRequest) -> dict[str, object]:
    try:
        weights = allocate(
            pd.DataFrame(request.returns),
            method=request.method,
            market_caps=pd.Series(request.market_caps) if request.market_caps else None,
            constraints=request.constraints.to_domain(),
            current_weights=(
                pd.Series(request.current_weights) if request.current_weights is not None else None
            ),
            industries=pd.Series(request.industries) if request.industries is not None else None,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"method": request.method, "weights": weights.to_dict()}


@router.post("/order-intents")
def order_intents(request: OrderIntentRequest) -> dict[str, object]:
    intents = generate_order_intents(
        pd.Series(request.target_weights),
        pd.Series(request.current_values),
        pd.Series(request.prices),
        total_assets=request.total_assets,
        lot_sizes=pd.Series(request.lot_sizes) if request.lot_sizes is not None else None,
        constraints=request.constraints.to_domain(),
    )
    return {
        "intents": [
            {
                "symbol": item.symbol,
                "direction": item.direction.value,
                "quantity": item.quantity,
                "target_weight": item.target_weight,
                "estimated_value": item.estimated_value,
                "reason": item.reason,
            }
            for item in intents
        ],
        "sent_to_broker": False,
    }


@router.post("/rebalance-plan")
def rebalance_plan(request: RebalancePlanRequest) -> dict[str, object]:
    try:
        plan = create_rebalance_plan(
            pd.Series(request.target_weights),
            pd.Series(request.current_values),
            pd.Series(request.prices),
            total_assets=request.total_assets,
            effective_at=request.effective_at,
            lot_sizes=pd.Series(request.lot_sizes) if request.lot_sizes is not None else None,
            industries=pd.Series(request.industries) if request.industries is not None else None,
            constraints=request.constraints.to_domain(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "effective_at": plan.effective_at.isoformat(),
        "target_weights": plan.target_weights,
        "target_values": plan.target_values,
        "expected_turnover": plan.expected_turnover,
        "cash_buffer": plan.cash_buffer,
        "industry_exposure": plan.industry_exposure,
        "intents": [
            {
                "symbol": item.symbol,
                "direction": item.direction.value,
                "quantity": item.quantity,
                "target_weight": item.target_weight,
                "estimated_value": item.estimated_value,
            }
            for item in plan.intents
        ],
        "sent_to_broker": plan.sent_to_broker,
    }
