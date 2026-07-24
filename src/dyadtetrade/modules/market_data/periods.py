from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

DERIVED_PERIOD_SOURCES: Final[dict[str, str]] = {
    "5m": "1m",
    "15m": "1m",
    "30m": "1m",
    "1h": "1m",
    "1w": "1d",
    "1q": "1d",
    "1y": "1d",
}


@dataclass(frozen=True)
class PeriodDerivation:
    target_period: str
    source_period: str


def derivation_for(target_period: str) -> PeriodDerivation:
    try:
        source = DERIVED_PERIOD_SOURCES[target_period]
    except KeyError as exc:
        raise ValueError(f"不支持派生周期：{target_period}") from exc
    return PeriodDerivation(target_period=target_period, source_period=source)


def resample_ohlcv(frame: pd.DataFrame, *, target_period: str) -> pd.DataFrame:
    """从已通过质量校验的 1m/1d 数据确定性地派生更大周期。

    周、季、年使用最后一个实际交易时间，而不是日历期末日期；分钟周期按
    自然时钟分桶，不向午休和非交易时段填充数据。
    """

    derivation_for(target_period)
    required = {"datetime", "symbol", "open", "high", "low", "close", "volume", "amount"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"派生周期缺少字段：{', '.join(missing)}")
    if frame.empty:
        return frame.copy()

    source = frame.copy()
    source["datetime"] = pd.to_datetime(source["datetime"], errors="raise")
    source = source.sort_values(["symbol", "datetime"])
    source["_bucket"] = _bucket_for(source["datetime"], target_period)
    grouped = source.groupby(["symbol", "_bucket"], sort=True, observed=True)
    aggregations: dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
    }
    for optional, operation in (
        ("pre_close", "first"),
        ("suspended", "all"),
        ("adjust_factor", "last"),
        ("adjust", "last"),
    ):
        if optional in source.columns:
            aggregations[optional] = operation

    result = grouped.agg(aggregations).dropna(subset=["open", "high", "low", "close"])
    actual_end = grouped["datetime"].max().rename("actual_datetime")
    result = result.join(actual_end).reset_index()
    result["datetime"] = result.pop("actual_datetime")
    result = result.drop(columns=["_bucket"])
    result["period"] = target_period
    if "suspended" in result:
        result["suspended"] = result["suspended"].astype(bool)
    return result.sort_values(["symbol", "datetime"]).reset_index(drop=True)


def _resample_rule(period: str) -> str:
    return {
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "60min",
        "1w": "W-FRI",
        "1q": "QE-DEC",
        "1y": "YE-DEC",
    }[period]


def _bucket_for(values: pd.Series, period: str) -> pd.Series:
    datetimes = pd.to_datetime(values)
    if period in {"5m", "15m", "30m", "1h"}:
        shifted = datetimes - pd.Timedelta(minutes=30)
        return shifted.dt.floor(_resample_rule(period))
    if period == "1w":
        return datetimes.dt.to_period("W-FRI").astype(str)
    if period == "1q":
        return datetimes.dt.to_period("Q-DEC").astype(str)
    return datetimes.dt.to_period("Y-DEC").astype(str)
