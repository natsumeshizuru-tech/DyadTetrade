from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from dyadtetrade.data.backtest.broker import HistoricalBroker
from dyadtetrade.data.backtest.models import Side
from dyadtetrade.data.compat.xtdata_compat import LocalXtData
from dyadtetrade.data.models import AdjustmentMode

CHINA_TZ = ZoneInfo("Asia/Shanghai")


class ContextInfo:
    def __init__(
        self,
        data: LocalXtData,
        broker: HistoricalBroker,
        *,
        period: str,
        timeline: list[int],
    ) -> None:
        self._data = data
        self._broker = broker
        self._universe: list[str] = []
        self._timeline = timeline
        self.period = period
        self.barpos = 0
        self.is_new_bar = True
        self.logs: list[tuple[int | None, str, str]] = []

    @property
    def current_ts(self) -> int:
        return self._timeline[self.barpos]

    @property
    def is_last_bar(self) -> bool:
        return self.barpos == len(self._timeline) - 1

    @property
    def time_tick_size(self) -> int:
        if len(self._timeline) < 2:
            return 0
        if self.barpos == 0:
            return self._timeline[1] - self._timeline[0]
        return self._timeline[self.barpos] - self._timeline[self.barpos - 1]

    def set_universe(self, symbols: list[str]) -> None:
        self._universe = list(dict.fromkeys(symbol.upper() for symbol in symbols))

    def get_universe(self) -> list[str]:
        return list(self._universe)

    def get_market_data(
        self,
        field_list: list[str] | None = None,
        stock_list: list[str] | None = None,
        period: str | None = None,
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        dividend_type: AdjustmentMode = "none",
        fill_data: bool = False,
    ) -> dict[str, pd.DataFrame]:
        return self._data.get_market_data(
            field_list,
            stock_list or self.get_universe(),
            period or self.period,
            start_time,
            self._bounded_end(end_time),
            count,
            dividend_type,
            fill_data,
        )

    def get_market_data_ex(
        self,
        field_list: list[str] | None = None,
        stock_list: list[str] | None = None,
        period: str | None = None,
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        dividend_type: AdjustmentMode = "none",
        fill_data: bool = False,
        subscribe: bool = False,
    ) -> dict[str, pd.DataFrame]:
        return self._data.get_market_data_ex(
            field_list,
            stock_list or self.get_universe(),
            period or self.period,
            start_time,
            self._bounded_end(end_time),
            count,
            dividend_type,
            fill_data,
            subscribe,
        )

    def order(self, symbol: str, quantity: int, side: Side) -> int:
        return self._broker.submit(symbol.upper(), side, quantity, self.current_ts)

    def log(self, message: str, *, level: str = "INFO") -> None:
        self.logs.append((self.current_ts, level, message))

    def _bounded_end(self, requested: str) -> str:
        current = datetime.fromtimestamp(self.current_ts / 1000, tz=CHINA_TZ).strftime(
            "%Y%m%d%H%M%S"
        )
        if not requested:
            return current
        digits = "".join(character for character in requested if character.isdigit())
        if not digits:
            raise ValueError(f"invalid end_time: {requested}")
        comparable = digits.ljust(14, "9")[:14]
        return requested if comparable <= current else current

    def account(self) -> dict[str, Any]:
        return {
            "cash": self._broker.cash,
            "positions": {
                symbol: {
                    "quantity": position.quantity,
                    "available": position.available,
                    "average_cost": position.average_cost,
                }
                for symbol, position in self._broker.positions.items()
            },
        }
