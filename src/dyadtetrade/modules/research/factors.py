from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class FactorAnalysisResult:
    summary: dict[str, float]
    daily_ic: pd.DataFrame
    group_returns: pd.DataFrame
    long_short: pd.DataFrame
    decay: pd.DataFrame
    turnover: pd.DataFrame
    correlation: pd.DataFrame
    exposures: pd.DataFrame


def winsorize(values: pd.Series, *, method: Literal["mad", "quantile"] = "mad") -> pd.Series:
    clean = values.astype(float).copy()
    if method == "quantile":
        lower, upper = clean.quantile([0.01, 0.99])
    else:
        median = clean.median()
        mad = (clean - median).abs().median()
        if not np.isfinite(mad) or mad == 0:
            return clean
        lower, upper = median - 5 * mad, median + 5 * mad
    return clean.clip(lower=lower, upper=upper)


def standardize(values: pd.Series) -> pd.Series:
    clean = values.astype(float)
    std = clean.std(ddof=0)
    return clean * 0 if not np.isfinite(std) or std == 0 else (clean - clean.mean()) / std


def fill_missing_cross_section(values: pd.Series, *, groups: pd.Series | None = None) -> pd.Series:
    """Fill only within the current cross-section; never carry values through time."""
    clean = values.astype(float).copy()
    if groups is None:
        return clean.fillna(clean.median())
    medians = clean.groupby(groups.astype(str)).transform("median")
    return clean.fillna(medians).fillna(clean.median())


def neutralize(
    factor: pd.Series, *, industry: pd.Series | None = None, market_cap: pd.Series | None = None
) -> pd.Series:
    frame = pd.DataFrame({"factor": factor.astype(float)})
    design = pd.DataFrame(index=frame.index)
    if market_cap is not None:
        cap = pd.Series(np.log(market_cap.astype(float).clip(lower=1)), index=market_cap.index)
        design["log_market_cap"] = standardize(cap)
    if industry is not None:
        design = pd.concat(
            [design, pd.get_dummies(industry.astype(str), prefix="industry", dtype=float)], axis=1
        )
    valid = frame["factor"].notna() & design.notna().all(axis=1)
    if not valid.any() or design.shape[1] == 0:
        return frame["factor"]
    x = np.column_stack([np.ones(int(valid.sum())), design.loc[valid].to_numpy(float)])
    y = frame.loc[valid, "factor"].to_numpy(float)
    coefficients = np.linalg.lstsq(x, y, rcond=None)[0]
    result = pd.Series(np.nan, index=frame.index, dtype=float)
    result.loc[valid] = y - x @ coefficients
    return result


def analyze_factor(
    data: pd.DataFrame,
    *,
    factor_columns: list[str],
    return_column: str = "forward_return",
    date_column: str = "date",
    symbol_column: str = "symbol",
    visible_at_column: str = "visible_at",
    groups: int = 5,
    max_decay: int = 5,
) -> FactorAnalysisResult:
    required = {date_column, symbol_column, return_column, visible_at_column, *factor_columns}
    missing = sorted(required.difference(data.columns))
    if missing:
        raise ValueError(f"factor data missing columns: {missing}")
    frame = data.copy()
    frame[date_column] = pd.to_datetime(frame[date_column])
    frame[visible_at_column] = pd.to_datetime(frame[visible_at_column])
    if (frame[visible_at_column] > frame[date_column]).any():
        raise ValueError("factor contains observations not visible at portfolio formation time")
    frame = frame.sort_values([date_column, symbol_column]).drop_duplicates(
        [date_column, symbol_column], keep="last"
    )

    ic_rows: list[dict[str, object]] = []
    group_rows: list[dict[str, object]] = []
    for trade_date, cross_section in frame.groupby(date_column, sort=True):
        for factor in factor_columns:
            subset = cross_section[[factor, return_column]].dropna()
            if len(subset) < 3:
                continue
            pearson = subset[factor].corr(subset[return_column], method="pearson")
            rank_ic = subset[factor].corr(subset[return_column], method="spearman")
            ic_rows.append(
                {"date": trade_date, "factor": factor, "pearson_ic": pearson, "rank_ic": rank_ic}
            )
            ranks = subset[factor].rank(method="first")
            bucket_count = min(groups, len(subset))
            labels = pd.qcut(ranks, bucket_count, labels=False, duplicates="drop")
            for group, value in subset.groupby(labels, observed=True)[return_column].mean().items():
                group_rows.append(
                    {
                        "date": trade_date,
                        "factor": factor,
                        "group": int(str(group)) + 1,
                        "return": value,
                    }
                )

    daily_ic = pd.DataFrame(ic_rows)
    group_returns = pd.DataFrame(group_rows)
    long_short_rows: list[dict[str, object]] = []
    if not group_returns.empty:
        for key, values in group_returns.groupby(["date", "factor"]):
            long_date = key[0]
            factor_name = str(key[1])
            ordered = values.sort_values("group")
            long_short_rows.append(
                {
                    "date": long_date,
                    "factor": factor_name,
                    "return": float(ordered["return"].iloc[-1] - ordered["return"].iloc[0]),
                }
            )
    long_short = pd.DataFrame(long_short_rows)

    decay_rows: list[dict[str, object]] = []
    indexed = frame.set_index([date_column, symbol_column])
    for factor in factor_columns:
        factor_series = indexed[factor]
        return_series = indexed[return_column]
        for lag in range(1, max_decay + 1):
            shifted = return_series.groupby(level=1).shift(-(lag - 1))
            decay_rows.append(
                {
                    "factor": factor,
                    "lag": lag,
                    "rank_ic": factor_series.corr(shifted, method="spearman"),
                }
            )
    decay = pd.DataFrame(decay_rows)
    turnover_rows: list[dict[str, object]] = []
    for factor in factor_columns:
        factor_ranks = frame.pivot(index=date_column, columns=symbol_column, values=factor).rank(
            axis=1, pct=True
        )
        ordered_dates = list(factor_ranks.index)
        for previous_date, current_date in zip(ordered_dates, ordered_dates[1:], strict=False):
            pair = pd.concat(
                [factor_ranks.loc[previous_date], factor_ranks.loc[current_date]],
                axis=1,
                keys=["previous", "current"],
            ).dropna()
            correlation_value = (
                float(pair["previous"].corr(pair["current"], method="spearman"))
                if len(pair) >= 3
                else float("nan")
            )
            turnover_rows.append(
                {
                    "date": current_date,
                    "factor": factor,
                    "turnover": (
                        0.5 * (1 - correlation_value)
                        if np.isfinite(correlation_value)
                        else float("nan")
                    ),
                }
            )
    turnover = pd.DataFrame(turnover_rows)
    correlation = frame[factor_columns].corr(method="spearman")
    exposures = frame.groupby(date_column)[factor_columns].agg(["mean", "std", "count"])

    summary: dict[str, float] = {
        "coverage": float(frame[factor_columns].notna().mean().mean()),
        "observations": float(len(frame)),
    }
    if not daily_ic.empty:
        rank_values = daily_ic["rank_ic"].dropna()
        summary["rank_ic_mean"] = float(rank_values.mean())
        summary["pearson_ic_mean"] = float(daily_ic["pearson_ic"].mean())
        rank_std = float(rank_values.std(ddof=1)) if len(rank_values) > 1 else 0.0
        summary["icir"] = float(rank_values.mean() / rank_std) if rank_std else 0.0
    return FactorAnalysisResult(
        summary,
        daily_ic,
        group_returns,
        long_short,
        decay,
        turnover,
        correlation,
        exposures,
    )
