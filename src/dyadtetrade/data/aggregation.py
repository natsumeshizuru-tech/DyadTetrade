from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

CHINA_TZ = ZoneInfo("Asia/Shanghai")
MINUTE_PERIODS = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}
DAILY_PERIODS = {"1w", "1mon", "1q", "1hy", "1y"}


def aggregate_bars(frame: pd.DataFrame, target_period: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if target_period in MINUTE_PERIODS:
        return _aggregate_minutes(frame, MINUTE_PERIODS[target_period])
    if target_period in DAILY_PERIODS:
        return _aggregate_daily(frame, target_period)
    raise ValueError(f"unsupported derived period: {target_period}")


def _with_datetime(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    timestamps = pd.to_datetime(result["ts"], unit="ms", utc=True).dt.tz_convert(CHINA_TZ)
    result["_datetime"] = timestamps
    return result


def _aggregate_minutes(frame: pd.DataFrame, size: int) -> pd.DataFrame:
    source = _with_datetime(frame)
    output: list[pd.DataFrame] = []
    for symbol, symbol_frame in source.groupby("symbol", sort=False):
        current = symbol_frame.copy()
        local_times = current["_datetime"].dt.time
        morning = (local_times >= time(9, 30)) & (local_times <= time(11, 30))
        afternoon = (local_times >= time(13, 0)) & (local_times <= time(15, 0))
        current = current.loc[morning | afternoon].copy()
        current["_session"] = "morning"
        current.loc[afternoon.loc[current.index], "_session"] = "afternoon"
        current["_session_start"] = current["_datetime"].dt.normalize()
        current.loc[current["_session"] == "morning", "_session_start"] += pd.Timedelta(
            hours=9, minutes=30
        )
        current.loc[current["_session"] == "afternoon", "_session_start"] += pd.Timedelta(hours=13)
        elapsed = (
            (current["_datetime"] - current["_session_start"]).dt.total_seconds() // 60
        ).astype(int)
        # XtData minute labels are normally bar-end labels (09:31..09:35).
        # Keep a possible 09:30/13:00 seed in the first bucket without ever
        # allowing the morning and afternoon sessions to share a bucket.
        current["_bucket"] = ((elapsed.clip(lower=1) - 1) // size).astype(int)
        grouped = current.groupby(["trade_date", "_session", "_bucket"], sort=True)
        aggregated = grouped.agg(
            ts=("ts", "max"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            amount=("amount", "sum"),
            settlement_price=("settlement_price", "last"),
            open_interest=("open_interest", "last"),
            pre_close=("pre_close", "first"),
            suspend_flag=("suspend_flag", "max"),
            source=("source", "last"),
            ingested_at=("ingested_at", "max"),
            source_hash=("source_hash", "last"),
        ).reset_index()
        aggregated.insert(0, "symbol", str(symbol))
        output.append(aggregated.drop(columns=["_session", "_bucket"]))
    return pd.concat(output, ignore_index=True).sort_values(["ts", "symbol"], kind="stable")


def _aggregate_daily(frame: pd.DataFrame, target_period: str) -> pd.DataFrame:
    source = _with_datetime(frame)
    rules = {"1w": "W-FRI", "1mon": "ME", "1q": "QE", "1hy": "2QE", "1y": "YE"}
    output: list[pd.DataFrame] = []
    for symbol, symbol_frame in source.groupby("symbol", sort=False):
        current = symbol_frame.set_index("_datetime").sort_index()
        aggregated = current.resample(rules[target_period]).agg(
            {
                "ts": "max",
                "trade_date": "last",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "amount": "sum",
                "settlement_price": "last",
                "open_interest": "last",
                "pre_close": "first",
                "suspend_flag": "max",
                "source": "last",
                "ingested_at": "max",
                "source_hash": "last",
            }
        )
        aggregated = aggregated.dropna(subset=["close"]).reset_index(drop=True)
        aggregated.insert(0, "symbol", str(symbol))
        output.append(aggregated)
    return pd.concat(output, ignore_index=True).sort_values(["ts", "symbol"], kind="stable")
