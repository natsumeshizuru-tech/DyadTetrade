from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

import pandas as pd

from dyadtetrade.data.backtest.broker import HistoricalBroker
from dyadtetrade.data.backtest.context_info import ContextInfo
from dyadtetrade.data.backtest.models import (
    BacktestRunResult,
    HistoricalBacktestSettings,
)
from dyadtetrade.data.backtest.repository import BacktestResultRepository
from dyadtetrade.data.compat.xtdata_compat import LocalXtData


class StrategyProtocol(Protocol):
    def init(self, context: ContextInfo) -> None: ...

    def handlebar(self, context: ContextInfo) -> None: ...


class QmtBacktestEngine:
    def __init__(
        self,
        data: LocalXtData,
        *,
        settings: HistoricalBacktestSettings | None = None,
        result_root: Path = Path("reports/backtests/sqlite"),
    ) -> None:
        self.data = data
        self.settings = settings or HistoricalBacktestSettings()
        self.result_root = result_root

    def run(
        self,
        strategy: StrategyProtocol,
        *,
        symbols: list[str],
        period: str,
        start: str,
        end: str,
        strategy_name: str | None = None,
    ) -> BacktestRunResult:
        normalized_symbols = [symbol.upper() for symbol in symbols]
        frames = self.data.get_market_data_ex(
            field_list=[
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "preClose",
                "suspendFlag",
            ],
            stock_list=normalized_symbols,
            period=period,
            start_time=start,
            end_time=end,
            count=-1,
            dividend_type="none",
            fill_data=False,
        )
        timeline = sorted(
            {
                int(timestamp.tz_localize("Asia/Shanghai").tz_convert("UTC").timestamp() * 1000)
                for frame in frames.values()
                for timestamp in pd.DatetimeIndex(frame.index)
            }
        )
        if not timeline:
            raise ValueError("backtest query returned no bars")
        indexed = {symbol: self._index_by_epoch(frame) for symbol, frame in frames.items()}
        broker = HistoricalBroker(self.settings)
        context = ContextInfo(self.data, broker, period=period, timeline=timeline)
        context.set_universe(normalized_symbols)
        strategy.init(context)
        for barpos, current_ts in enumerate(timeline):
            context.barpos = barpos
            current_bars = {
                symbol: dict(frame.loc[current_ts])
                for symbol, frame in indexed.items()
                if current_ts in frame.index
            }
            if not current_bars:
                continue
            trade_date = int(
                pd.Timestamp(current_ts, unit="ms", tz="UTC")
                .tz_convert("Asia/Shanghai")
                .strftime("%Y%m%d")
            )
            broker.begin_trade_date(trade_date)
            broker.execute_pending(current_bars, current_ts)
            strategy.handlebar(context)
            broker.mark_to_market(
                current_ts,
                {
                    symbol: bar.get("close")
                    for symbol, bar in current_bars.items()
                    if bar.get("close") is not None
                },
            )
        run_id = f"backtest-{uuid.uuid4().hex}"
        database = self.result_root / f"{run_id}.db"
        result = BacktestRunResult(
            run_id=run_id,
            start_ts=timeline[0],
            end_ts=timeline[-1],
            bars_processed=len(timeline),
            final_cash=broker.cash,
            final_assets=broker.equity[-1].total_assets,
            orders=tuple(broker.orders),
            trades=tuple(broker.trades),
            equity=tuple(broker.equity),
            result_database=str(database),
        )
        repository = BacktestResultRepository(database)
        repository.save(
            result,
            strategy_name=strategy_name or type(strategy).__name__,
            period=period,
            config={
                "symbols": normalized_symbols,
                "start": start,
                "end": end,
                "settings": self.settings,
            },
            positions=broker.positions,
            logs=context.logs,
        )
        return result

    @staticmethod
    def _index_by_epoch(frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        index = pd.DatetimeIndex(result.index)
        if index.tz is None:
            index = index.tz_localize("Asia/Shanghai")
        else:
            index = index.tz_convert("Asia/Shanghai")
        result.index = [int(value.tz_convert("UTC").timestamp() * 1000) for value in index]
        return result
