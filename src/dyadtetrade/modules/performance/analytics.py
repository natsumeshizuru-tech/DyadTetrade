from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class PerformanceResult:
    metrics: dict[str, float | str | None]
    equity: pd.DataFrame
    monthly_returns: pd.DataFrame
    annual_returns: pd.DataFrame
    rolling: pd.DataFrame


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if np.isfinite(denominator) and denominator != 0 else 0.0


def analyze_performance(
    equity: pd.DataFrame,
    *,
    benchmark: pd.Series | None = None,
    risk_free_rate: float = 0.0,
    annualization: int = 252,
    trade_pnls: pd.Series | None = None,
    turnover_notional: float = 0.0,
    trade_count: int = 0,
    position_values: pd.Series | None = None,
) -> PerformanceResult:
    required = {"date", "total_assets"}
    missing = sorted(required.difference(equity.columns))
    if missing:
        raise ValueError(f"equity data missing columns: {missing}")
    frame = equity.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    values = frame["total_assets"].astype(float)
    if len(values) < 2 or (values <= 0).any():
        raise ValueError("performance analysis requires at least two positive equity values")
    returns = values.pct_change().fillna(0.0)
    total_return = float(values.iloc[-1] / values.iloc[0] - 1)
    years = max(len(returns) / annualization, 1 / annualization)
    annual_return = float((1 + total_return) ** (1 / years) - 1) if total_return > -1 else -1.0
    annual_volatility = float(returns.std(ddof=1) * sqrt(annualization))
    excess_return = returns - risk_free_rate / annualization
    sharpe = _safe_ratio(float(excess_return.mean() * annualization), annual_volatility)
    downside = returns.clip(upper=0).std(ddof=1) * sqrt(annualization)
    sortino = _safe_ratio(float(excess_return.mean() * annualization), float(downside))

    running_max = values.cummax()
    drawdown = values / running_max - 1
    max_drawdown = float(drawdown.min())
    drawdown_end_position = int(np.argmin(drawdown.to_numpy()))
    drawdown_end = values.index[drawdown_end_position]
    prior = values.iloc[: drawdown_end_position + 1]
    drawdown_start = prior.idxmax()
    calmar = _safe_ratio(annual_return, abs(max_drawdown))

    aligned_benchmark: pd.Series | None = None
    alpha = beta = information_ratio = 0.0
    excess_drawdown = 0.0
    if benchmark is not None and not benchmark.empty:
        benchmark_values = benchmark.astype(float).reindex(returns.index).ffill()
        benchmark_returns = benchmark_values.pct_change().fillna(0.0)
        valid = returns.notna() & benchmark_returns.notna()
        aligned_benchmark = benchmark_values
        variance = float(benchmark_returns.loc[valid].var(ddof=1))
        beta = (
            float(returns.loc[valid].cov(benchmark_returns.loc[valid]) / variance)
            if variance
            else 0.0
        )
        alpha = float(
            (returns.loc[valid].mean() - beta * benchmark_returns.loc[valid].mean()) * annualization
        )
        active = returns.loc[valid] - benchmark_returns.loc[valid]
        information_ratio = _safe_ratio(
            float(active.mean() * annualization),
            float(active.std(ddof=1) * sqrt(annualization)),
        )
        relative = (1 + active).cumprod()
        excess_drawdown = float((relative / relative.cummax() - 1).min())

    wins = losses = pd.Series(dtype=float)
    if trade_pnls is not None:
        cleaned_pnls = trade_pnls.astype(float).dropna()
        wins = cleaned_pnls[cleaned_pnls > 0]
        losses = cleaned_pnls[cleaned_pnls < 0]
    win_rate = float(len(wins) / (len(wins) + len(losses))) if len(wins) + len(losses) else 0.0
    average_win = float(wins.mean()) if len(wins) else 0.0
    average_loss = abs(float(losses.mean())) if len(losses) else 0.0
    profit_loss_ratio = _safe_ratio(average_win, average_loss)
    profit_factor = _safe_ratio(float(wins.sum()), abs(float(losses.sum())))
    turnover = _safe_ratio(turnover_notional, float(values.mean()))
    concentration = 0.0
    if position_values is not None:
        cleaned_positions = position_values.astype(float).clip(lower=0)
        if cleaned_positions.sum() > 0:
            weights = cleaned_positions / cleaned_positions.sum()
            concentration = float((weights**2).sum())

    frame["return"] = returns
    frame["net_value"] = values / values.iloc[0]
    frame["drawdown"] = drawdown
    frame["underwater"] = drawdown
    if aligned_benchmark is not None and not aligned_benchmark.dropna().empty:
        frame["benchmark"] = aligned_benchmark
        frame["relative_net_value"] = frame["net_value"] / (
            aligned_benchmark / aligned_benchmark.dropna().iloc[0]
        )

    monthly = (1 + returns).resample("ME").prod() - 1
    annual = (1 + returns).resample("YE").prod() - 1
    rolling_window = min(20, max(2, len(returns)))
    rolling = pd.DataFrame(index=returns.index)
    rolling["rolling_return"] = (1 + returns).rolling(rolling_window).apply(np.prod, raw=True) - 1
    rolling["rolling_volatility"] = returns.rolling(rolling_window).std() * sqrt(annualization)
    rolling["rolling_sharpe"] = (
        returns.rolling(rolling_window).mean() * annualization / rolling["rolling_volatility"]
    )

    metrics: dict[str, float | str | None] = {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "max_drawdown": max_drawdown,
        "max_drawdown_start": str(drawdown_start),
        "max_drawdown_end": str(drawdown_end),
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "information_ratio": information_ratio,
        "beta": beta,
        "alpha": alpha,
        "win_rate": win_rate,
        "profit_loss_ratio": profit_loss_ratio,
        "profit_factor": profit_factor,
        "turnover": turnover,
        "trade_count": float(trade_count),
        "holding_concentration": concentration,
        "excess_drawdown": excess_drawdown,
        "observations": float(len(returns)),
    }
    return PerformanceResult(
        metrics,
        frame.reset_index(),
        monthly.rename("return").reset_index(),
        annual.rename("return").reset_index(),
        rolling.reset_index(),
    )
