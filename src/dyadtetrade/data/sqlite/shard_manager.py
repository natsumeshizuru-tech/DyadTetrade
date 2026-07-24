from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dyadtetrade.data.config import StorageConfig
from dyadtetrade.data.models import BarRecord

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def symbol_market(symbol: str) -> str:
    try:
        _, market = symbol.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError(f"invalid symbol: {symbol}") from exc
    market = market.upper()
    if market not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"unsupported market in symbol: {symbol}")
    return market


class ShardManager:
    def __init__(self, storage: StorageConfig) -> None:
        self.storage = storage
        self.root = storage.root

    @property
    def catalog_path(self) -> Path:
        return self.root / self.storage.catalog

    @property
    def daily_path(self) -> Path:
        return self.root / self.storage.daily_bars

    def minute_path(self, market: str, year_month: str) -> Path:
        normalized_market = market.upper()
        if normalized_market not in {"SH", "SZ", "BJ"}:
            raise ValueError(f"unsupported market: {market}")
        if len(year_month) != 6 or not year_month.isdigit():
            raise ValueError(f"invalid year-month: {year_month}")
        return self.root / f"bars_1m_{normalized_market}_{year_month}.db"

    def tick_path(self, market: str, trade_date: int) -> Path:
        return self.root / f"ticks_{market.upper()}_{trade_date:08d}.db"

    def fundamentals_path(self, year: int) -> Path:
        if year < 1990 or year > 9999:
            raise ValueError(f"invalid fundamentals year: {year}")
        return self.root / f"fundamentals_{year:04d}.db"

    def path_for(self, period: str, symbol: str, ts: int, trade_date: int) -> Path:
        if period == "1d":
            return self.daily_path
        market = symbol_market(symbol)
        if period == "1m":
            local = datetime.fromtimestamp(ts / 1000, tz=CHINA_TZ)
            return self.minute_path(market, local.strftime("%Y%m"))
        if period == "tick":
            return self.tick_path(market, trade_date)
        raise ValueError(f"only raw 1d, 1m and tick data can be stored, got {period}")

    def group_bars(self, period: str, bars: list[BarRecord]) -> dict[Path, list[BarRecord]]:
        grouped: defaultdict[Path, list[BarRecord]] = defaultdict(list)
        for bar in bars:
            grouped[self.path_for(period, bar.symbol, bar.ts, bar.trade_date)].append(bar)
        return dict(grouped)

    def minute_paths_between(self, symbols: list[str], start: int, end: int) -> list[Path]:
        start_text = str(start)
        end_text = str(end)
        if len(start_text) != 8 or len(end_text) != 8:
            raise ValueError("date boundaries must use YYYYMMDD integers")
        year = int(start_text[:4])
        month = int(start_text[4:6])
        end_pair = (int(end_text[:4]), int(end_text[4:6]))
        months: list[str] = []
        while (year, month) <= end_pair:
            months.append(f"{year:04d}{month:02d}")
            month += 1
            if month == 13:
                year += 1
                month = 1
        markets = sorted({symbol_market(symbol) for symbol in symbols})
        return [self.minute_path(market, item) for item in months for market in markets]
