from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from dyadtetrade.domain import Direction


@dataclass(frozen=True, slots=True)
class PortfolioConstraints:
    max_single_weight: float = 0.2
    max_total_weight: float = 0.95
    cash_buffer: float = 0.05
    min_trade_value: float = 1000.0
    min_rebalance_weight: float = 0.005
    turnover_limit: float = 1.0
    industry_limits: dict[str, float] = field(default_factory=dict)
    risk_budgets: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PortfolioOrderIntent:
    symbol: str
    direction: Direction
    quantity: int
    target_weight: float
    estimated_value: float
    reason: str = "rebalance"


@dataclass(frozen=True, slots=True)
class RebalancePlan:
    effective_at: datetime
    target_weights: dict[str, float]
    target_values: dict[str, float]
    intents: tuple[PortfolioOrderIntent, ...]
    expected_turnover: float
    cash_buffer: float
    industry_exposure: dict[str, float]
    sent_to_broker: bool = False


def allocate(
    returns: pd.DataFrame,
    *,
    method: Literal[
        "equal",
        "market_cap",
        "inverse_volatility",
        "risk_parity",
        "minimum_variance",
        "mean_variance",
        "maximum_diversification",
    ] = "equal",
    market_caps: pd.Series | None = None,
    constraints: PortfolioConstraints | None = None,
    current_weights: pd.Series | None = None,
    industries: pd.Series | None = None,
) -> pd.Series:
    constraints = constraints or PortfolioConstraints()
    if returns.empty or returns.shape[1] == 0:
        raise ValueError("portfolio allocation requires return observations")
    columns = list(returns.columns)
    target_total = min(constraints.max_total_weight, 1 - constraints.cash_buffer)
    weights: NDArray[np.float64]
    if method == "equal":
        weights = np.full(len(columns), target_total / len(columns))
    elif method == "market_cap":
        if market_caps is None:
            raise ValueError("market_cap allocation requires market_caps")
        caps = market_caps.reindex(columns).astype(float).clip(lower=0)
        weights = np.asarray(caps.to_numpy() / caps.sum() * target_total, dtype=np.float64)
    elif method == "inverse_volatility":
        volatility = returns.std(ddof=1).replace(0, np.nan)
        inverse = (1 / volatility).fillna(0)
        weights = np.asarray(inverse.to_numpy() / inverse.sum() * target_total, dtype=np.float64)
    elif method == "risk_parity":
        weights = _risk_parity_weights(
            returns.cov().to_numpy(),
            columns,
            constraints.risk_budgets,
            target_total,
        )
    else:
        cp: Any = importlib.import_module("cvxpy")
        covariance = returns.cov().to_numpy()
        expected = returns.mean().to_numpy()
        variable = cp.Variable(len(columns))
        constraints_list = [
            variable >= 0,
            variable <= constraints.max_single_weight,
            cp.sum(variable) == target_total,
        ]
        _add_cvx_constraints(
            constraints_list,
            variable,
            columns,
            constraints,
            current_weights=current_weights,
            industries=industries,
            cp=cp,
        )
        if method == "minimum_variance":
            objective = cp.Minimize(cp.quad_form(variable, covariance))
        elif method == "mean_variance":
            objective = cp.Maximize(expected @ variable - 5 * cp.quad_form(variable, covariance))
        else:
            volatility = np.sqrt(np.diag(covariance))
            objective = cp.Maximize(volatility @ variable - 5 * cp.quad_form(variable, covariance))
        problem = cp.Problem(objective, constraints_list)
        problem.solve(solver=cp.CLARABEL)
        if variable.value is None:
            raise ValueError("portfolio optimization did not converge")
        weights = np.asarray(variable.value, dtype=float)
    weights = np.clip(weights, 0, constraints.max_single_weight)
    feasible_total = min(target_total, len(weights) * constraints.max_single_weight)
    if weights.sum() > 0:
        weights = weights / weights.sum() * min(feasible_total, weights.sum())
        for _ in range(len(weights)):
            remaining = feasible_total - float(weights.sum())
            if remaining <= 1e-12:
                break
            headroom = np.maximum(constraints.max_single_weight - weights, 0)
            if headroom.sum() <= 0:
                break
            weights += np.minimum(headroom, headroom / headroom.sum() * remaining)
    if method in {"equal", "market_cap", "inverse_volatility", "risk_parity"} and (
        constraints.industry_limits or current_weights is not None
    ):
        weights = _project_weights(
            weights,
            columns,
            constraints,
            current_weights=current_weights,
            industries=industries,
            target_total=feasible_total,
        )
    return pd.Series(weights, index=columns, name="target_weight")


def _risk_parity_weights(
    covariance: NDArray[np.float64],
    columns: Sequence[str],
    budgets: dict[str, float],
    target_total: float,
) -> NDArray[np.float64]:
    count = len(columns)
    raw_budgets = np.asarray([max(float(budgets.get(str(item), 1.0)), 0.0) for item in columns])
    if raw_budgets.sum() <= 0:
        raise ValueError("risk budgets must contain a positive value")
    normalized_budgets = raw_budgets / raw_budgets.sum()
    weights = normalized_budgets.copy()
    regularized = np.asarray(covariance, dtype=np.float64) + np.eye(count) * 1e-12
    for _ in range(500):
        marginal = regularized @ weights
        contributions = weights * marginal
        total_risk = float(contributions.sum())
        if total_risk <= 0:
            break
        target = normalized_budgets * total_risk
        updated = weights * np.sqrt(np.maximum(target, 1e-16) / np.maximum(contributions, 1e-16))
        updated = np.maximum(updated, 1e-12)
        updated /= updated.sum()
        if np.max(np.abs(updated - weights)) < 1e-10:
            weights = updated
            break
        weights = updated
    return np.asarray(weights * target_total, dtype=np.float64)


def _add_cvx_constraints(
    constraints_list: list[Any],
    variable: Any,
    columns: Sequence[str],
    constraints: PortfolioConstraints,
    *,
    current_weights: pd.Series | None,
    industries: pd.Series | None,
    cp: Any,
) -> None:
    if constraints.industry_limits:
        if industries is None:
            raise ValueError("industry limits require an industries series")
        aligned = industries.reindex(list(columns)).astype(str)
        for industry, limit in constraints.industry_limits.items():
            indices = [index for index, value in enumerate(aligned) if value == industry]
            if indices:
                constraints_list.append(cp.sum(variable[indices]) <= float(limit))
    if current_weights is not None:
        current = current_weights.reindex(list(columns)).fillna(0).astype(float).to_numpy()
        constraints_list.append(cp.norm1(variable - current) <= constraints.turnover_limit)


def _project_weights(
    proposed: NDArray[np.float64],
    columns: Sequence[str],
    constraints: PortfolioConstraints,
    *,
    current_weights: pd.Series | None,
    industries: pd.Series | None,
    target_total: float,
) -> NDArray[np.float64]:
    cp: Any = importlib.import_module("cvxpy")
    variable = cp.Variable(len(columns))
    cvx_constraints: list[Any] = [
        variable >= 0,
        variable <= constraints.max_single_weight,
        cp.sum(variable) == target_total,
    ]
    _add_cvx_constraints(
        cvx_constraints,
        variable,
        columns,
        constraints,
        current_weights=current_weights,
        industries=industries,
        cp=cp,
    )
    problem = cp.Problem(cp.Minimize(cp.sum_squares(variable - proposed)), cvx_constraints)
    problem.solve(solver=cp.CLARABEL)
    if variable.value is None:
        raise ValueError("portfolio constraints are infeasible")
    return np.asarray(variable.value, dtype=np.float64)


def generate_order_intents(
    target_weights: pd.Series,
    current_values: pd.Series,
    prices: pd.Series,
    *,
    total_assets: float,
    lot_sizes: pd.Series | None = None,
    constraints: PortfolioConstraints | None = None,
) -> list[PortfolioOrderIntent]:
    constraints = constraints or PortfolioConstraints()
    candidates: list[tuple[str, float, float, int, float]] = []
    lot_sizes = lot_sizes if lot_sizes is not None else pd.Series(100, index=target_weights.index)
    for symbol, weight_value in target_weights.items():
        symbol_text = str(symbol)
        weight = float(weight_value)
        current = float(current_values.get(symbol_text, 0.0))
        price = float(prices.get(symbol_text, 0.0))
        lot = int(lot_sizes.get(symbol_text, 100))
        if price <= 0 or lot <= 0:
            continue
        difference = total_assets * weight - current
        if abs(difference) < constraints.min_trade_value:
            continue
        if abs(difference) / total_assets < constraints.min_rebalance_weight:
            continue
        quantity = (
            int(
                (Decimal(str(abs(difference))) / Decimal(str(price)) / lot).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
            * lot
        )
        if quantity:
            candidates.append((symbol_text, difference, price, lot, weight))
    intents: list[PortfolioOrderIntent] = []
    remaining_turnover = max(constraints.turnover_limit, 0) * total_assets
    for symbol_text, difference, price, lot, weight in sorted(
        candidates, key=lambda item: (item[1] > 0, item[0])
    ):
        desired_quantity = int(abs(difference) / price / lot) * lot
        affordable_quantity = int(remaining_turnover / price / lot) * lot
        quantity = min(desired_quantity, affordable_quantity)
        if quantity <= 0:
            continue
        estimated_value = quantity * price
        intents.append(
            PortfolioOrderIntent(
                symbol_text,
                Direction.BUY if difference > 0 else Direction.SELL,
                quantity,
                weight,
                estimated_value,
            )
        )
        remaining_turnover -= estimated_value
    return intents


def create_rebalance_plan(
    target_weights: pd.Series,
    current_values: pd.Series,
    prices: pd.Series,
    *,
    total_assets: float,
    effective_at: datetime,
    lot_sizes: pd.Series | None = None,
    industries: pd.Series | None = None,
    constraints: PortfolioConstraints | None = None,
) -> RebalancePlan:
    constraints = constraints or PortfolioConstraints()
    intents = generate_order_intents(
        target_weights,
        current_values,
        prices,
        total_assets=total_assets,
        lot_sizes=lot_sizes,
        constraints=constraints,
    )
    target_values = {
        str(symbol): float(weight) * total_assets for symbol, weight in target_weights.items()
    }
    industry_exposure: dict[str, float] = {}
    if industries is not None:
        for symbol, weight in target_weights.items():
            industry = str(industries.get(str(symbol), "unknown"))
            industry_exposure[industry] = industry_exposure.get(industry, 0.0) + float(weight)
    for industry, limit in constraints.industry_limits.items():
        if industry_exposure.get(industry, 0.0) > limit + 1e-8:
            raise ValueError(f"industry exposure exceeds limit: {industry}")
    turnover = sum(item.estimated_value for item in intents) / total_assets
    return RebalancePlan(
        effective_at,
        {str(symbol): float(weight) for symbol, weight in target_weights.items()},
        target_values,
        tuple(intents),
        turnover,
        max(0.0, 1.0 - float(target_weights.sum())),
        industry_exposure,
    )
