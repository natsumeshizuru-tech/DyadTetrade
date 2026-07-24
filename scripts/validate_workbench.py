from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import backtrader as bt  # type: ignore[import-untyped]
import pandas as pd

from dyadtetrade.core.config import get_settings
from dyadtetrade.db.duckdb_store import query_bars
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.domain import Bar, Interval
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.integrations.xtquant_converters import tick_from_xtdata
from dyadtetrade.modules.backtest import BacktestConfig, BacktestEngine
from dyadtetrade.modules.backtest.service import BacktestService, task_to_dict
from dyadtetrade.modules.optimization import (
    BacktestOptimizationConfig,
    BacktestOptimizationService,
)
from dyadtetrade.modules.paper_trading import PaperStrategyRunner, PaperTradingService
from dyadtetrade.modules.portfolio import allocate, generate_order_intents
from dyadtetrade.modules.research import analyze_factor

STOCKS = [
    "600519.SH",
    "000001.SZ",
    "000333.SZ",
    "600036.SH",
    "601318.SH",
    "300750.SZ",
    "000858.SZ",
    "600276.SH",
    "601166.SH",
    "002594.SZ",
]
ETFS = ["510300.SH", "510500.SH", "159915.SZ", "512100.SH"]
START = date(2026, 5, 6)
END = date(2026, 7, 10)


def load_frame(symbols: list[str]) -> pd.DataFrame:
    records = query_bars(
        symbols=symbols,
        start_date=START.strftime("%Y%m%d"),
        end_date=END.strftime("%Y%m%d"),
        layer="bt_ready",
        limit=100_000,
    )
    if not records:
        raise RuntimeError(f"no bt_ready records for {symbols}")
    return pd.DataFrame(records)


def config(
    strategy: str,
    symbols: list[str],
    parameters: dict[str, object],
) -> BacktestConfig:
    return BacktestConfig(
        strategy_name=strategy,
        strategy_parameters=parameters,
        symbols=symbols,
        start_date=START,
        end_date=END,
        initial_cash=Decimal("1000000"),
        commission_rate=Decimal("0.0003"),
        minimum_commission=Decimal("5"),
        stamp_duty_rate=Decimal("0.0005"),
        slippage_bps=Decimal("0"),
        max_volume_participation=Decimal("0.1"),
        random_seed=7,
    )


async def persistent_backtest(
    name: str, frame: pd.DataFrame, parameters: dict[str, object], symbols: list[str]
) -> dict[str, object]:
    service = BacktestService()
    async with SessionFactory() as session:
        frame_hash = hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()[:12]
        task, _ = await service.create_task(
            session,
            config(name, symbols, parameters),
            idempotency_key=f"validation-report-v3-{name}-{'-'.join(symbols)}-{frame_hash}",
        )
        task = await service.run_task(session, task.id, frame=frame)
        return task_to_dict(task)


class BacktraderBuyHold(bt.Strategy):  # type: ignore[misc]
    def __init__(self) -> None:
        self.submitted = False

    def next(self) -> None:
        if not self.submitted:
            self.buy(size=100)
            self.submitted = True


def compare_engines(frame: pd.DataFrame) -> dict[str, float]:
    ordered = frame.sort_values("datetime").copy()
    own = BacktestEngine().run(
        config("buy_and_hold", ["600519.SH"], {"symbol": "600519.SH", "quantity": 100}),
        ordered,
    )
    first_fill = float(ordered.iloc[1]["open"])
    final_close = float(ordered.iloc[-1]["close"])
    fee = max(first_fill * 100 * 0.0003, 5.0)
    vectorized = 1_000_000 - first_fill * 100 - fee + final_close * 100

    feed = ordered[["datetime", "open", "high", "low", "close", "volume"]].copy()
    feed["openinterest"] = 0
    feed = feed.set_index(pd.to_datetime(feed.pop("datetime")))
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(1_000_000)
    cerebro.broker.setcommission(commission=0.0003)
    cerebro.adddata(bt.feeds.PandasData(dataname=feed))
    cerebro.addstrategy(BacktraderBuyHold)
    cerebro.run()
    backtrader_value = float(cerebro.broker.getvalue())
    own_value = float(own.daily_equity[-1].total_assets)
    return {
        "dyadtetrade": own_value,
        "vectorized": vectorized,
        "backtrader": backtrader_value,
        "dyadtetrade_minus_vectorized": own_value - vectorized,
        "backtrader_minus_vectorized": backtrader_value - vectorized,
    }


def factor_validation(frame: pd.DataFrame) -> dict[str, float]:
    ordered = frame.sort_values(["symbol", "datetime"]).copy()
    ordered["factor"] = ordered.groupby("symbol")["close"].pct_change(5)
    ordered["forward_return"] = ordered.groupby("symbol")["close"].transform(
        lambda values: values.shift(-1) / values - 1
    )
    ordered["date"] = pd.to_datetime(ordered["datetime"])
    ordered["visible_at"] = ordered["date"]
    result = analyze_factor(
        ordered.dropna(subset=["factor", "forward_return"]),
        factor_columns=["factor"],
        groups=5,
        max_decay=3,
    )
    return result.summary


async def optimization_validation(frame: pd.DataFrame) -> dict[str, object]:
    service = BacktestOptimizationService()
    optimization_config = BacktestOptimizationConfig(
        base_backtest=config(
            "moving_average_cross",
            ["510300.SH"],
            {"symbol": "510300.SH", "quantity": 10000},
        ),
        parameter_space={"fast": [2, 3], "slow": [5, 8, 10]},
        method="grid",
        max_trials=6,
        parallel_workers=2,
        random_seed=7,
        objective_weights={"total_return": 1.0, "max_drawdown": 0.2},
        walk_forward_folds=2,
        monte_carlo_simulations=100,
    )
    frame_hash = hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()[:12]
    async with SessionFactory() as session:
        task, _ = await service.create_task(
            session,
            optimization_config,
            idempotency_key=f"validation-robust-optimization-v1-{frame_hash}",
        )
        task = await service.run_task(session, task.id, frame=frame)
    payload = json.loads(task.result_json or "{}")
    result = payload["optimization"]
    robustness = payload["robustness"]
    return {
        "task_id": task.id,
        "status": task.status,
        "trials": len(result["trials"]),
        "best_parameters": result["best"]["parameters"],
        "pareto_front": len(result["pareto_front"]),
        "robustness_scenarios": len(robustness["scenarios"]),
        "walk_forward_folds": len(robustness["walk_forward"]),
        "monte_carlo_simulations": robustness["monte_carlo"]["simulations"],
        "warnings": [*result["warnings"], *robustness["warnings"]],
    }


def etf_portfolio_validation(frame: pd.DataFrame) -> dict[str, object]:
    closes = frame.pivot(index="datetime", columns="symbol", values="close").sort_index()
    returns = closes.pct_change().dropna()
    weights = allocate(returns, method="inverse_volatility")
    latest = closes.iloc[-1]
    intents = generate_order_intents(
        weights,
        pd.Series(0.0, index=weights.index),
        latest,
        total_assets=1_000_000,
    )
    return {
        "weights": {str(key): float(value) for key, value in weights.items()},
        "weight_sum": float(weights.sum()),
        "order_intents": len(intents),
        "sent_to_broker": False,
    }


def paper_realtime_validation() -> dict[str, object]:
    gateway = XtQuantGateway()
    snapshot = gateway.full_tick(["510300.SH"]).get("510300.SH", {})
    now = datetime.now(UTC).replace(microsecond=0)
    tick = tick_from_xtdata("510300.SH", snapshot, received_at=now)
    price = tick.last_price
    paper = PaperTradingService(initial_cash=Decimal("100000"))
    first = Bar(
        "510300.SH",
        now,
        Interval.MINUTE_1,
        price,
        price,
        price,
        price,
        100000,
        source="miniqmt-full-tick",
    )
    runner = PaperStrategyRunner(
        paper,
        "buy_and_hold",
        "validation-paper-buy-hold",
        {"symbol": "510300.SH", "quantity": 100},
    )
    runner.start()
    runner.on_bars(now, [first])
    order = next(iter(paper.oms.orders.values()))
    runner.on_bars(
        now + timedelta(minutes=1),
        [
            first.__class__(
                first.symbol,
                now + timedelta(minutes=1),
                first.interval,
                first.open,
                first.high,
                first.low,
                first.close,
                first.volume,
                source=first.source,
            )
        ],
    )
    return {
        "miniqmt_price": float(price),
        "order_status": order.status.value,
        "paper_trade_count": len(paper.oms.trades),
        "strategy_name": runner.strategy.name,
        "strategy_running": runner.running,
        "live_order_sent": False,
        "reconciled": paper.last_reconciled_at is not None,
    }


async def main() -> None:
    if get_settings().enable_live_trading:
        raise RuntimeError("validation requires ENABLE_LIVE_TRADING=false")
    await initialize_database()
    stock_frame = load_frame(STOCKS)
    etf_frame = load_frame(ETFS)
    ma_frame = etf_frame.loc[etf_frame["symbol"] == "510300.SH"].copy()
    buy_frame = stock_frame.loc[stock_frame["symbol"] == "600519.SH"].copy()
    results: dict[str, Any] = {
        "live_trading_enabled": False,
        "data": {
            "stocks": len(STOCKS),
            "etfs": len(ETFS),
            "stock_rows": len(stock_frame),
            "etf_rows": len(etf_frame),
        },
        "buy_and_hold_600519": await persistent_backtest(
            "buy_and_hold", buy_frame, {"symbol": "600519.SH", "quantity": 100}, ["600519.SH"]
        ),
        "ma_510300": await persistent_backtest(
            "moving_average_cross",
            ma_frame,
            {"symbol": "510300.SH", "fast": 5, "slow": 15, "quantity": 10000},
            ["510300.SH"],
        ),
        "rotation_10_stocks": await persistent_backtest(
            "equal_weight_rotation",
            stock_frame,
            {"symbols": STOCKS, "lookback": 10, "top_n": 3, "rebalance_every": 5},
            STOCKS,
        ),
        "etf_portfolio": etf_portfolio_validation(etf_frame),
        "factor_ic": factor_validation(stock_frame),
        "optimization": await optimization_validation(ma_frame),
        "paper_realtime": paper_realtime_validation(),
        "engine_comparison": compare_engines(buy_frame),
    }
    output = Path("reports/validation/workbench_validation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
