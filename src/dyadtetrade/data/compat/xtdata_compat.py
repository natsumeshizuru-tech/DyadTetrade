from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from dyadtetrade.data.adjustments import adjust_prices
from dyadtetrade.data.aggregation import DAILY_PERIODS, MINUTE_PERIODS, aggregate_bars
from dyadtetrade.data.config import DataPlatformSettings, load_data_config
from dyadtetrade.data.models import AdjustmentMode
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository

DEFAULT_FIELDS = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "settle",
    "openInterest",
    "preClose",
    "suspendFlag",
]
FIELD_MAP = {
    "time": "time",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
    "settle": "settlement_price",
    "openInterest": "open_interest",
    "preClose": "pre_close",
    "suspendFlag": "suspend_flag",
}


class LocalXtData:
    def __init__(self, settings: DataPlatformSettings | None = None) -> None:
        self.settings = settings or load_data_config()
        self.bars = BarRepository(self.settings.storage)
        self.catalog = CatalogRepository(self.settings.storage)

    def get_market_data_ex(
        self,
        field_list: list[str] | None = None,
        stock_list: list[str] | None = None,
        period: str = "1d",
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        dividend_type: AdjustmentMode = "none",
        fill_data: bool = True,
        subscribe: bool = False,
    ) -> dict[str, pd.DataFrame]:
        if subscribe:
            raise ValueError("LocalXtData is historical-only and does not subscribe")
        symbols = [symbol.upper() for symbol in stock_list or []]
        if not symbols:
            return {}
        start = _compact_date(start_time, default=19900101)
        end = _compact_date(end_time, default=29991231)
        base_period = (
            "1m" if period in MINUTE_PERIODS else "1d" if period in DAILY_PERIODS else period
        )
        if base_period not in {"1d", "1m"}:
            raise ValueError(f"unsupported local period: {period}")
        raw = self.bars.query(symbols, period=base_period, start_date=start, end_date=end)
        start_ts = _exact_epoch(start_time, end_of_day=False)
        end_ts = _exact_epoch(end_time, end_of_day=True)
        if start_ts is not None and not raw.empty:
            raw = raw.loc[raw["ts"] >= start_ts]
        if end_ts is not None and not raw.empty:
            raw = raw.loc[raw["ts"] <= end_ts]
        if period != base_period and not raw.empty:
            raw = aggregate_bars(raw, period)
        fields = field_list or DEFAULT_FIELDS
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            frame = raw.loc[raw["symbol"] == symbol].copy()
            action_start = 19900101 if dividend_type in {"back", "back_ratio"} else start
            actions = pd.DataFrame(
                [dict(row) for row in self.catalog.corporate_actions([symbol], action_start, end)]
            )
            frame = adjust_prices(frame, actions, mode=dividend_type)
            frame = self._xt_frame(frame, fields)
            if fill_data:
                frame = self._fill(frame, base_period, start, end)
            if count > 0:
                frame = frame.tail(count)
            result[symbol] = frame
        return result

    def get_market_data(
        self,
        field_list: list[str] | None = None,
        stock_list: list[str] | None = None,
        period: str = "1d",
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        dividend_type: AdjustmentMode = "none",
        fill_data: bool = True,
    ) -> dict[str, pd.DataFrame]:
        fields = field_list or DEFAULT_FIELDS
        by_symbol = self.get_market_data_ex(
            fields,
            stock_list,
            period,
            start_time,
            end_time,
            count,
            dividend_type,
            fill_data,
        )
        result: dict[str, pd.DataFrame] = {}
        for field in fields:
            rows = {
                symbol: frame[field]
                for symbol, frame in by_symbol.items()
                if field in frame.columns
            }
            result[field] = pd.DataFrame(rows).T if rows else pd.DataFrame()
        return result

    def get_local_data(self, *args: Any, **kwargs: Any) -> dict[str, pd.DataFrame]:
        return self.get_market_data_ex(*args, **kwargs)

    def get_trading_calendar(
        self, market: str, start_time: str = "", end_time: str = ""
    ) -> list[str]:
        days = self.catalog.open_days(
            market.upper(),
            _compact_date(start_time, default=19900101),
            _compact_date(end_time, default=29991231),
        )
        return [str(day) for day in days]

    def get_stock_list_in_sector(
        self, sector_name: str, snapshot_date: int | None = None
    ) -> list[str]:
        return self.catalog.sector_symbols(sector_name, snapshot_date=snapshot_date)

    def get_instrument_detail(self, stock_code: str, iscomplete: bool = False) -> dict[str, Any]:
        del iscomplete
        return self.catalog.instrument(stock_code.upper()) or {}

    def get_divid_factors(
        self, stock_code: str, start_time: str = "", end_time: str = ""
    ) -> pd.DataFrame:
        rows = self.catalog.corporate_actions(
            [stock_code.upper()],
            _compact_date(start_time, default=19900101),
            _compact_date(end_time, default=29991231),
        )
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(row) for row in rows]).set_index("action_date")

    @staticmethod
    def _xt_frame(frame: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=fields)
        local_time = pd.to_datetime(frame["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
        frame["time"] = frame["ts"]
        selected: dict[str, pd.Series[Any]] = {}
        for field in fields:
            storage_field = FIELD_MAP.get(field)
            if storage_field is None:
                raise ValueError(f"unsupported XtData field: {field}")
            selected[field] = frame[storage_field]
        result = pd.DataFrame(selected)
        result.index = pd.DatetimeIndex(local_time.dt.tz_localize(None), name="stime")
        return result

    def _fill(self, frame: pd.DataFrame, base_period: str, start: int, end: int) -> pd.DataFrame:
        if frame.empty or base_period != "1d":
            return frame
        days = self.catalog.open_days("SH", start, end)
        expected = pd.DatetimeIndex(pd.to_datetime([str(day) for day in days]), name="stime")
        filled = frame.reindex(expected)
        price_fields = [field for field in ("open", "high", "low", "close") if field in filled]
        if "close" in filled:
            previous_close = filled["close"].ffill()
            for field in price_fields:
                filled[field] = filled[field].fillna(previous_close)
        for field in ("volume", "amount"):
            if field in filled:
                filled[field] = filled[field].fillna(0)
        return filled


def configure_local_xtdata(
    path: Path | str = Path("config/market_data.yaml"),
) -> LocalXtData:
    return LocalXtData(load_data_config(path))


def _compact_date(value: str, *, default: int) -> int:
    if not value:
        return default
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) < 8:
        raise ValueError(f"date must contain YYYYMMDD: {value}")
    return int(digits[:8])


def _exact_epoch(value: str, *, end_of_day: bool) -> int | None:
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) <= 8:
        return None
    if len(digits) >= 14:
        text = digits[:14]
    elif len(digits) == 12:
        text = digits + ("59" if end_of_day else "00")
    elif len(digits) == 10:
        text = digits + ("5959" if end_of_day else "0000")
    else:
        raise ValueError(f"unsupported time precision: {value}")
    local = pd.Timestamp(datetime.strptime(text, "%Y%m%d%H%M%S"), tz="Asia/Shanghai")
    return int(local.tz_convert("UTC").timestamp() * 1000)
