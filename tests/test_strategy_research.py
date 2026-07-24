from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from dyadtetrade.domain import Account, Bar, Direction, Interval, Order, OrderRequest, Position
from dyadtetrade.modules.research import analyze_factor, neutralize, standardize, winsorize
from dyadtetrade.modules.strategy import StrategyContext, strategy_registry


class FakeRuntime:
    def __init__(self) -> None:
        self._now = datetime(2026, 1, 5, tzinfo=UTC)
        self.account = Account("paper", Decimal("100000"), Decimal("100000"))
        self.orders: list[Order] = []
        self.bar = Bar(
            "600519.SH",
            self._now,
            Interval.DAY_1,
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            10000,
        )

    @property
    def now(self) -> datetime:
        return self._now

    def history(self, symbols: list[str], count: int, fields: list[str]) -> pd.DataFrame:
        del fields
        return pd.DataFrame(
            {
                "symbol": [symbol for symbol in symbols for _ in range(count)],
                "close": [float(index + 1) for _ in symbols for index in range(count)],
            }
        )

    def current(self, symbol: str) -> Bar | None:
        return self.bar if symbol == self.bar.symbol else None

    def get_account(self) -> Account:
        return self.account

    def submit_order(self, request: OrderRequest) -> Order:
        order = Order(f"O{len(self.orders) + 1}", request)
        self.orders.append(order)
        return order

    def cancel_order(self, request: object) -> Order | None:
        del request
        return None

    def schedule(self, name: str, delay: timedelta, callback: object) -> None:
        del name, delay, callback

    def record(self, values: dict[str, float]) -> None:
        del values


def test_strategy_registry_and_buy_hold_context() -> None:
    runtime = FakeRuntime()
    strategy = strategy_registry.create(
        "buy_and_hold", "strategy-1", {"symbol": "600519.SH", "quantity": 100}
    )
    context = StrategyContext(strategy.strategy_id, runtime)
    strategy.on_bar(context, runtime.bar)
    strategy.on_bar(context, runtime.bar)
    assert len(runtime.orders) == 1
    assert runtime.orders[0].request.direction is Direction.BUY
    assert runtime.orders[0].request.strategy_id == "strategy-1"
    state = strategy.snapshot(runtime.now)
    restored = strategy_registry.create(
        "buy_and_hold", "strategy-1", {"symbol": "600519.SH", "quantity": 100}
    )
    restored.restore(state)
    assert restored.state["submitted"] is True
    names = {item.name for item in strategy_registry.list_strategies()}
    assert "etf_multi_asset_allocation" in names


def test_strategy_parameters_reject_invalid_windows() -> None:
    with pytest.raises(ValueError, match="fast window"):
        strategy_registry.create(
            "moving_average_cross", "bad", {"symbol": "510300.SH", "fast": 20, "slow": 5}
        )


def test_target_reduction_respects_t1_sellable_quantity() -> None:
    runtime = FakeRuntime()
    runtime.account.positions["600519.SH"] = Position(
        "600519.SH",
        quantity=1000,
        available_quantity=0,
        average_cost=Decimal("100"),
        last_price=Decimal("100"),
    )
    context = StrategyContext("target-test", runtime)
    assert context.order_target_percent("600519.SH", Decimal("0")) is None
    runtime.account.positions["600519.SH"].available_quantity = 500
    order = context.order_target_percent("600519.SH", Decimal("0"))
    assert order is not None
    assert order.request.direction is Direction.SELL
    assert order.request.quantity == 500


def test_factor_analysis_and_visible_time_gate() -> None:
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    rows = []
    for date in dates:
        for index, symbol in enumerate(["A", "B", "C", "D", "E"]):
            rows.append(
                {
                    "date": date,
                    "visible_at": date,
                    "symbol": symbol,
                    "factor": float(index),
                    "forward_return": float(index) / 100,
                }
            )
    frame = pd.DataFrame(rows)
    result = analyze_factor(frame, factor_columns=["factor"], groups=5, max_decay=2)
    assert result.summary["rank_ic_mean"] == pytest.approx(1.0)
    assert result.summary["coverage"] == 1.0
    assert len(result.group_returns) == 20
    future = frame.copy()
    future.loc[0, "visible_at"] = future.loc[0, "date"] + pd.Timedelta(days=1)
    with pytest.raises(ValueError, match="not visible"):
        analyze_factor(future, factor_columns=["factor"])


def test_factor_preprocessing() -> None:
    values = pd.Series([1.0, 2.0, 3.0, 1000.0])
    assert winsorize(values).max() < 1000
    assert standardize(values).mean() == pytest.approx(0)
    residual = neutralize(
        pd.Series([1.0, 2.0, 3.0, 4.0]),
        industry=pd.Series(["a", "a", "b", "b"]),
        market_cap=pd.Series([10.0, 20.0, 30.0, 40.0]),
    )
    assert residual.notna().all()
