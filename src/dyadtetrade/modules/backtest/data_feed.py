from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import pandas as pd

from dyadtetrade.domain import Bar, Interval


def decimal_value(value: object, default: Decimal | None = None) -> Decimal | None:
    if value is None or bool(pd.Series([value]).isna().iloc[0]):
        return default
    return Decimal(str(value))


class DataFrameFeed:
    def __init__(self, frame: pd.DataFrame, *, interval: Interval = Interval.DAY_1) -> None:
        required = {"datetime", "symbol", "open", "high", "low", "close", "volume"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"backtest feed missing columns: {missing}")
        normalized = frame.copy()
        normalized["datetime"] = pd.to_datetime(normalized["datetime"])
        if normalized.duplicated(["datetime", "symbol"]).any():
            raise ValueError("backtest feed contains duplicate symbol timestamps")
        self.frame = normalized.sort_values(["datetime", "symbol"]).reset_index(drop=True)
        self.interval = interval

    def __iter__(self) -> Iterator[tuple[datetime, list[Bar]]]:
        for timestamp, rows in self.frame.groupby("datetime", sort=True):
            bars = [self._row_to_bar(row) for _, row in rows.iterrows()]
            yield pd.Timestamp(str(timestamp)).to_pydatetime(), bars

    def fingerprint_payload(self) -> bytes:
        columns = ["datetime", "symbol", "open", "high", "low", "close", "volume"]
        return self.frame[columns].to_csv(index=False).encode()

    def _row_to_bar(self, row: pd.Series) -> Bar:
        close = decimal_value(row["close"])
        assert close is not None
        return Bar(
            symbol=str(row["symbol"]),
            timestamp=pd.Timestamp(row["datetime"]).to_pydatetime(),
            interval=self.interval,
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=close,
            volume=int(row["volume"]),
            amount=decimal_value(row.get("amount"), Decimal("0")) or Decimal("0"),
            pre_close=decimal_value(row.get("pre_close")),
            limit_up=decimal_value(row.get("limit_up")),
            limit_down=decimal_value(row.get("limit_down")),
            suspended=bool(row.get("suspended", False)),
            adjustment=str(row.get("adjust", "none")),
        )
