from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

CHINA_TZ = "Asia/Shanghai"

OHLCV_FIELDS = ["open", "high", "low", "close", "volume", "amount"]
TICK_FIELDS = [
    "time",
    "lastPrice",
    "open",
    "high",
    "low",
    "lastClose",
    "volume",
    "amount",
    "bidPrice",
    "askPrice",
    "bidVol",
    "askVol",
]
STANDARD_FIELDS = [
    "datetime",
    "symbol",
    *OHLCV_FIELDS,
    "pre_close",
    "suspended",
    "adjust_factor",
    "period",
    "adjust",
    "bar_start_time",
    "source",
    "is_final",
    "received_at",
    "updated_at",
    "quality_status",
]

TICK_STANDARD_FIELDS = [
    "datetime",
    "symbol",
    "last_price",
    "open",
    "high",
    "low",
    "pre_close",
    "volume",
    "amount",
    "bid_price",
    "ask_price",
    "bid_volume",
    "ask_volume",
    "period",
    "adjust",
    "source_level",
    "cadence_seconds",
    "source",
    "quality_status",
]


def normalize_datetime(values: Any) -> pd.DatetimeIndex:
    series = pd.Series(values)
    text = series.astype(str).str.strip()

    # Use an object staging series because pandas cannot safely assign timezone-aware
    # values into the naive datetime64 result produced by the compact YYYYMMDD pass.
    result = pd.Series(pd.NaT, index=series.index, dtype=object)
    compact = text.str.fullmatch(r"\d{8}")
    if compact.any():
        parsed_compact = pd.to_datetime(text.loc[compact], format="%Y%m%d", errors="coerce")
        result.loc[compact] = parsed_compact.tolist()
    missing = result.isna()
    if missing.any():
        parsed = pd.to_datetime(text.loc[missing], format="mixed", errors="coerce")
        result.loc[missing] = parsed.tolist()
    missing = result.isna()
    if missing.any():
        numeric = pd.to_numeric(text.loc[missing], errors="coerce")
        milliseconds = pd.to_datetime(numeric, unit="ms", errors="coerce")
        result.loc[missing] = milliseconds.tolist()

    if result.isna().any():
        raise ValueError("行情数据包含无法解析的日期")
    return pd.DatetimeIndex(result.tolist())


def normalize_tick_datetime(values: Any) -> pd.DatetimeIndex:
    """Interpret XtData epoch ticks as UTC instants and expose China-local time."""

    series = pd.Series(values)
    numeric = pd.to_numeric(series, errors="coerce")
    output = pd.Series(pd.NaT, index=series.index, dtype=f"datetime64[ns, {CHINA_TZ}]")
    numeric_mask = numeric.notna()
    if numeric_mask.any():
        milliseconds = numeric_mask & (numeric.abs() > 10**11)
        seconds = numeric_mask & ~milliseconds
        if milliseconds.any():
            output.loc[milliseconds] = pd.to_datetime(
                numeric.loc[milliseconds], unit="ms", utc=True, errors="coerce"
            ).dt.tz_convert(CHINA_TZ)
        if seconds.any():
            output.loc[seconds] = pd.to_datetime(
                numeric.loc[seconds], unit="s", utc=True, errors="coerce"
            ).dt.tz_convert(CHINA_TZ)
    text_mask = ~numeric_mask
    if text_mask.any():
        local = pd.to_datetime(series.loc[text_mask].astype(str), errors="coerce")
        if local.dt.tz is None:
            local = local.dt.tz_localize(CHINA_TZ, ambiguous="NaT", nonexistent="NaT")
        else:
            local = local.dt.tz_convert(CHINA_TZ)
        output.loc[text_mask] = local
    if output.isna().any():
        raise ValueError("Tick 数据包含无法解析的日期")
    return pd.DatetimeIndex(output)


def raw_to_tabular(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    result = frame.copy()
    if not isinstance(result.index, pd.RangeIndex) or result.index.name:
        result = result.reset_index()
    if "datetime" not in result.columns:
        first = result.columns[0]
        if str(first).lower() in {"index", "time", "date", "timetag", "datetime"}:
            result = result.rename(columns={first: "datetime"})
    if "symbol" in result.columns:
        result["symbol"] = symbol
    else:
        result.insert(0, "symbol", symbol)
    return result


def normalize_ohlcv(
    frame: pd.DataFrame,
    *,
    symbol: str,
    period: str,
    adjust: str,
    asset_type: str | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [str(column).strip().lower() for column in result.columns]
    result = result.rename(
        columns={
            "preclose": "pre_close",
            "lastclose": "pre_close",
            "suspendflag": "suspended",
            "suspend_flag": "suspended",
            "factor": "adjust_factor",
        }
    )

    datetime_column = next(
        (column for column in ("datetime", "time", "date", "timetag") if column in result.columns),
        None,
    )
    if datetime_column:
        datetimes = normalize_datetime(result.pop(datetime_column))
    else:
        datetimes = normalize_datetime(result.index)
    result.index = datetimes
    result.index.name = "datetime"

    missing = [field for field in ("open", "high", "low", "close") if field not in result]
    if missing:
        raise ValueError(f"行情数据缺少必要字段：{', '.join(missing)}")

    for field in OHLCV_FIELDS:
        if field not in result:
            result[field] = 0.0 if field in {"volume", "amount"} else np.nan
        result[field] = pd.to_numeric(result[field], errors="coerce")

    if "pre_close" not in result:
        result["pre_close"] = result["close"].shift(1)
    result["pre_close"] = pd.to_numeric(result["pre_close"], errors="coerce")
    if "adjust_factor" not in result:
        result["adjust_factor"] = 1.0
    result["adjust_factor"] = pd.to_numeric(result["adjust_factor"], errors="coerce").fillna(1.0)
    index_missing_turnover = pd.Series(False, index=result.index)
    if asset_type == "index":
        index_missing_turnover = (result[["volume", "amount"]] < 0).any(axis=1)
        for field in ("volume", "amount"):
            result.loc[result[field] < 0, field] = 0
    if "suspended" in result:
        suspended = pd.to_numeric(result["suspended"], errors="coerce").fillna(0).astype(bool)
    else:
        suspended = (result["volume"].fillna(0) == 0) & (result["amount"].fillna(0) == 0)
    if asset_type == "index":
        suspended.loc[index_missing_turnover] = False
    result["suspended"] = suspended

    nonpositive = (result[["open", "high", "low", "close"]] <= 0).any(axis=1)
    zero_price_suspensions = nonpositive & result["suspended"]
    if zero_price_suspensions.any():
        result = result.loc[~zero_price_suspensions].copy()
    if result.empty:
        raise ValueError("行情数据仅包含零价格停牌行")

    result = result[[*OHLCV_FIELDS, "pre_close", "suspended", "adjust_factor"]].sort_index()
    result = result[~result.index.duplicated(keep="last")]
    result = result.dropna(subset=["open", "high", "low", "close"])

    if (result[["open", "high", "low", "close"]] <= 0).any(axis=None):
        raise ValueError("行情数据存在非正数 OHLC")
    if (result["high"] < result[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("行情数据存在 high 小于 open/close/low")
    if (result["low"] > result[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("行情数据存在 low 大于 open/close/high")
    if asset_type != "index" and (result[["volume", "amount"]] < 0).any(axis=None):
        raise ValueError("行情数据存在负成交量或成交额")

    quality_status = pd.Series("valid", index=result.index)
    if asset_type == "index":
        quality_status.loc[index_missing_turnover.reindex(result.index, fill_value=False)] = (
            "price_only"
        )
    result = result.reset_index()
    result.insert(1, "symbol", symbol)
    result["period"] = period
    result["adjust"] = adjust
    result["bar_start_time"] = result["datetime"]
    result["source"] = "xtdata_history_or_import"
    result["is_final"] = True
    result["received_at"] = pd.NaT
    result["updated_at"] = pd.Timestamp.now(tz="Asia/Shanghai")
    result["quality_status"] = quality_status.to_numpy()
    return result[STANDARD_FIELDS]


def normalize_tick(
    frame: pd.DataFrame,
    *,
    symbol: str,
    expected_days: list[str] | None = None,
) -> pd.DataFrame:
    """Normalize XtData historical ticks without pretending that ticks are bars."""

    result = frame.copy()
    result.columns = [str(column).strip() for column in result.columns]
    aliases = {
        "lastPrice": "last_price",
        "lastprice": "last_price",
        "lastClose": "pre_close",
        "lastclose": "pre_close",
        "bidPrice": "bid_price",
        "askPrice": "ask_price",
        "bidVol": "bid_volume",
        "askVol": "ask_volume",
    }
    result = result.rename(columns=aliases)
    datetime_column = next(
        (column for column in ("datetime", "time", "timetag") if column in result.columns),
        None,
    )
    if datetime_column is None:
        datetimes = normalize_tick_datetime(result.index)
    else:
        datetimes = normalize_tick_datetime(result.pop(datetime_column))
    result.index = datetimes
    result.index.name = "datetime"
    required = {"last_price", "volume", "amount"}
    missing = sorted(required - set(result.columns))
    if missing:
        raise ValueError(f"Tick 数据缺少字段：{', '.join(missing)}")
    for field in (
        "last_price",
        "open",
        "high",
        "low",
        "pre_close",
        "volume",
        "amount",
    ):
        if field not in result:
            result[field] = result["last_price"] if field in {"open", "high", "low"} else np.nan
        result[field] = pd.to_numeric(result[field], errors="coerce")
    for field in ("bid_price", "ask_price", "bid_volume", "ask_volume"):
        if field not in result:
            result[field] = None
    result = result.dropna(subset=["last_price"])
    datetime_index = pd.DatetimeIndex(result.index)
    dates = pd.Series(datetime_index.strftime("%Y%m%d"), index=result.index)
    valid_dates = pd.Series(datetime_index.weekday < 5, index=result.index)
    if expected_days is not None:
        valid_dates &= dates.isin(set(expected_days))
    result = result.loc[valid_dates]
    if result.empty or (result["last_price"] <= 0).any():
        raise ValueError("Tick 数据为空或包含非正最新价")
    if (result[["volume", "amount"]] < 0).any(axis=None):
        raise ValueError("Tick 数据包含负成交量或成交额")
    result = result.reset_index().drop_duplicates(subset=["datetime"], keep="last")
    result.insert(1, "symbol", symbol)
    result["period"] = "tick"
    result["adjust"] = "none"
    result["source_level"] = "level1"
    result["cadence_seconds"] = 3
    result["source"] = "xtdata_history"
    result["quality_status"] = "valid"
    return result[TICK_STANDARD_FIELDS]
