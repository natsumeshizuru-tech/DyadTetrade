from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime
from math import isnan
from typing import Any, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from dyadtetrade.data.models import BarRecord

CHINA_TZ = ZoneInfo("Asia/Shanghai")
PRICE_ALIASES = {
    "settle": "settlement_price",
    "settlementprice": "settlement_price",
    "openinterest": "open_interest",
    "preclose": "pre_close",
    "lastclose": "pre_close",
    "suspendflag": "suspend_flag",
}
CANONICAL_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "settlement_price",
    "open_interest",
    "pre_close",
    "suspend_flag",
)


def _timestamp(value: object, *, period: str) -> pd.Timestamp:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        magnitude = abs(float(value))
        unit = "ms" if magnitude > 100_000_000_000 else "s"
        return pd.Timestamp(value, unit=unit, tz="UTC").tz_convert(CHINA_TZ)
    text = str(value).strip()
    if text.isdigit() and len(text) in {8, 14, 17}:
        if len(text) == 8:
            parsed = datetime.strptime(text, "%Y%m%d")
        elif len(text) == 14:
            parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
        else:
            parsed = datetime.strptime(text, "%Y%m%d%H%M%S%f")
        return pd.Timestamp(parsed, tz=CHINA_TZ)
    parsed = pd.Timestamp(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(CHINA_TZ)
    else:
        parsed = parsed.tz_convert(CHINA_TZ)
    if period == "1d":
        return parsed.normalize()
    return parsed


def normalize_xtdata_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    period: str,
    source: str = "xtdata",
    ingested_at: int | None = None,
) -> list[BarRecord]:
    if period not in {"1d", "1m"}:
        raise ValueError(f"raw bar normalization supports 1d and 1m, got {period}")
    normalized = frame.copy()
    normalized.columns = [str(column).replace("_", "").lower() for column in normalized.columns]
    normalized = normalized.rename(columns=PRICE_ALIASES)
    time_column = next(
        (column for column in ("time", "datetime", "timetag", "date") if column in normalized),
        None,
    )
    raw_times = normalized.pop(time_column) if time_column else normalized.index
    timestamps = _timestamps(raw_times, period=period)
    required = {"open", "high", "low", "close"}
    missing = sorted(required - set(normalized.columns))
    if missing:
        raise ValueError(f"XtData frame is missing required columns: {', '.join(missing)}")
    for column in CANONICAL_COLUMNS:
        if column not in normalized:
            normalized[column] = None
    normalized = pd.DataFrame(normalized.loc[:, list(CANONICAL_COLUMNS)])
    numeric = normalized.apply(pd.to_numeric, errors="coerce")
    numeric_values = numeric.to_numpy(dtype=np.float64, na_value=np.nan)
    hashes = pd.util.hash_pandas_object(numeric, index=False, categorize=True).to_numpy()
    epoch_ms = np.asarray(cast(Any, timestamps.tz_convert("UTC")).asi8) // 1_000_000
    trade_dates = np.asarray(timestamps.strftime("%Y%m%d"), dtype=np.int32)
    now = ingested_at if ingested_at is not None else time.time_ns() // 1_000_000
    records: list[BarRecord] = []
    for timestamp_ms, trade_date, values, row_hash in zip(
        epoch_ms, trade_dates, numeric_values, hashes, strict=True
    ):
        records.append(
            BarRecord(
                symbol=symbol.upper(),
                ts=int(timestamp_ms),
                trade_date=int(trade_date),
                open=_numeric_float(values[0]),
                high=_numeric_float(values[1]),
                low=_numeric_float(values[2]),
                close=_numeric_float(values[3]),
                volume=_numeric_int(values[4]),
                amount=_numeric_float(values[5]),
                settlement_price=_numeric_float(values[6]),
                open_interest=_numeric_float(values[7]),
                pre_close=_numeric_float(values[8]),
                suspend_flag=_numeric_int(values[9]),
                source=source,
                ingested_at=now,
                source_hash=f"{int(row_hash):016x}",
            )
        )
    records.sort(key=lambda item: (item.symbol, item.ts))
    return records


def _timestamps(values: Iterable[object], *, period: str) -> pd.DatetimeIndex:
    raw = list(values)
    texts = [str(value).strip() for value in raw]
    lengths = {len(value) for value in texts if value.isdigit()}
    if len(lengths) == 1 and len(texts) == sum(value.isdigit() for value in texts):
        length = next(iter(lengths), 0)
        formats = {8: "%Y%m%d", 14: "%Y%m%d%H%M%S", 17: "%Y%m%d%H%M%S%f"}
        if length in formats:
            parsed = pd.DatetimeIndex(pd.to_datetime(texts, format=formats[length]))
            return parsed.tz_localize(CHINA_TZ)
    return pd.DatetimeIndex([_timestamp(value, period=period) for value in raw])


def _numeric_float(value: np.float64) -> float | None:
    return None if isnan(value) else float(value)


def _numeric_int(value: np.float64) -> int | None:
    return None if isnan(value) else int(value)
