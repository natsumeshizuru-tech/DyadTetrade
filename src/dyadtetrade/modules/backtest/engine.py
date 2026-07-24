from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd

from dyadtetrade.modules.backtest.broker import SimulatedBroker
from dyadtetrade.modules.backtest.commission import AShareCommission
from dyadtetrade.modules.backtest.data_feed import DataFrameFeed
from dyadtetrade.modules.backtest.matcher import BarMatcher
from dyadtetrade.modules.backtest.result import BacktestResult, DailyEquity
from dyadtetrade.modules.backtest.schemas import BacktestConfig
from dyadtetrade.modules.performance import analyze_performance
from dyadtetrade.modules.strategy import StrategyContext, strategy_registry


class BacktestEngine:
    def run(self, config: BacktestConfig, frame: pd.DataFrame) -> BacktestResult:
        benchmark = self.benchmark_series(config, frame)
        feed = DataFrameFeed(self._filter_frame(config, frame), interval=config.interval)
        fingerprint = hashlib.sha256(feed.fingerprint_payload()).hexdigest()
        strategy = strategy_registry.create(
            config.strategy_name,
            f"backtest-{uuid4().hex[:12]}",
            {**config.strategy_parameters, "random_seed": config.random_seed},
        )
        broker = SimulatedBroker(
            initial_cash=config.initial_cash,
            commission=AShareCommission(
                config.commission_rate,
                config.minimum_commission,
                config.stamp_duty_rate,
                config.transfer_fee_rate,
            ),
            matcher=BarMatcher(
                config.slippage_bps,
                config.max_volume_participation,
                config.conservative_limit_board,
                config.allow_partial_fills,
            ),
            enforce_t1=config.enforce_t1,
            execution_delay_bars=config.execution_delay_bars,
        )
        context = StrategyContext(strategy.strategy_id, broker)
        strategy.on_init(context)
        strategy.on_start(context)
        equity: list[DailyEquity] = []
        last_date: date | None = None
        peak = config.initial_cash
        previous_assets = config.initial_cash
        benchmark_base: Decimal | None = None
        for timestamp, bars in feed:
            broker.set_time(timestamp)
            if last_date != timestamp.date():
                broker.settle_new_day()
                last_date = timestamp.date()
            trades = broker.match(bars)
            for trade in trades:
                strategy.on_trade(context, trade)
            broker.mark(bars)
            for bar in bars:
                strategy.on_bar(context, bar)
            broker.run_scheduled()
            assets = broker.account.total_assets
            broker.account.assert_identity(assets)
            peak = max(peak, assets)
            benchmark_net_value: Decimal | None = None
            if benchmark is not None:
                visible_benchmark = benchmark.loc[benchmark.index <= pd.Timestamp(timestamp)]
                if not visible_benchmark.empty:
                    benchmark_value = Decimal(str(float(visible_benchmark.iloc[-1])))
                    benchmark_base = benchmark_base or benchmark_value
                    if benchmark_base > 0:
                        benchmark_net_value = benchmark_value / benchmark_base
            net_value = assets / config.initial_cash
            equity.append(
                DailyEquity(
                    date=timestamp.isoformat(),
                    cash=broker.account.cash,
                    positions_value=broker.account.positions_value,
                    total_assets=assets,
                    return_value=(
                        assets / previous_assets - Decimal("1") if previous_assets else Decimal("0")
                    ),
                    drawdown=assets / peak - Decimal("1") if peak else Decimal("0"),
                    net_value=net_value,
                    benchmark_net_value=benchmark_net_value,
                    relative_net_value=(
                        net_value / benchmark_net_value
                        if benchmark_net_value is not None and benchmark_net_value > 0
                        else None
                    ),
                )
            )
            previous_assets = assets
        strategy.on_stop(context)
        result = BacktestResult(
            run_id=uuid4().hex,
            config_snapshot=json.loads(config.model_dump_json()),
            data_fingerprint=fingerprint,
            strategy_version=strategy.version,
            orders=broker.orders,
            trades=broker.trades,
            daily_equity=equity,
            records=broker.records,
            final_positions={
                symbol: position.market_value
                for symbol, position in broker.account.positions.items()
                if position.quantity > 0
            },
        )
        turnover = sum(float(trade.notional) for trade in result.trades)
        performance = analyze_performance(
            result.equity_frame(),
            turnover_notional=turnover,
            trade_count=len(result.trades),
            position_values=pd.Series(result.final_positions, dtype=float),
            benchmark=benchmark,
        )
        result.metrics = {
            key: float(value)
            for key, value in performance.metrics.items()
            if isinstance(value, int | float)
        }
        return result

    @staticmethod
    def benchmark_series(config: BacktestConfig, frame: pd.DataFrame) -> pd.Series | None:
        if not config.benchmark_symbol or "symbol" not in frame or "datetime" not in frame:
            return None
        selected = frame.loc[frame["symbol"] == config.benchmark_symbol, ["datetime", "close"]]
        if selected.empty:
            return None
        selected = selected.copy()
        selected["datetime"] = pd.to_datetime(selected["datetime"])
        selected = selected.sort_values("datetime").drop_duplicates("datetime", keep="last")
        return selected.set_index("datetime")["close"].astype(float)

    @staticmethod
    def _filter_frame(config: BacktestConfig, frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.loc[frame["symbol"].isin(config.symbols)].copy()
        timestamps = pd.to_datetime(result["datetime"])
        if config.start_date is not None:
            result = result.loc[timestamps.dt.date >= config.start_date]
            timestamps = pd.to_datetime(result["datetime"])
        if config.end_date is not None:
            result = result.loc[timestamps.dt.date <= config.end_date]
        if result.empty:
            raise ValueError("backtest has no data after applying symbols and date range")
        return result
