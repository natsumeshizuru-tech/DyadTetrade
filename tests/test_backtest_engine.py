from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from dyadtetrade.domain import (
    Bar,
    CancelRequest,
    Direction,
    Exchange,
    Instrument,
    InstrumentType,
    Interval,
    OrderRequest,
    OrderStatus,
    OrderType,
)
from dyadtetrade.modules.backtest import BacktestConfig, BacktestEngine
from dyadtetrade.modules.backtest.broker import SimulatedBroker
from dyadtetrade.modules.backtest.commission import AShareCommission
from dyadtetrade.modules.backtest.matcher import BarMatcher


def sample_frame(symbol: str = "600519.SH") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-05", periods=4, freq="B"),
            "symbol": [symbol] * 4,
            "open": [10.0, 11.0, 10.5, 12.0],
            "high": [10.5, 11.5, 11.0, 12.5],
            "low": [9.5, 10.5, 10.0, 11.5],
            "close": [10.0, 11.0, 10.8, 12.0],
            "volume": [10000] * 4,
            "amount": [100000.0] * 4,
            "suspended": [False] * 4,
        }
    )


def make_bar(
    *,
    timestamp: datetime,
    open_price: str = "10",
    volume: int = 10000,
    suspended: bool = False,
    limit_up: str | None = None,
    limit_down: str | None = None,
) -> Bar:
    opening = Decimal(open_price)
    return Bar(
        "600519.SH",
        timestamp,
        Interval.DAY_1,
        opening,
        opening,
        opening,
        opening,
        volume,
        suspended=suspended,
        limit_up=Decimal(limit_up) if limit_up else None,
        limit_down=Decimal(limit_down) if limit_down else None,
    )


def make_broker(*, participation: str = "1", slippage: str = "0") -> SimulatedBroker:
    return SimulatedBroker(
        initial_cash=Decimal("100000"),
        commission=AShareCommission(),
        matcher=BarMatcher(
            slippage_bps=Decimal(slippage),
            max_volume_participation=Decimal(participation),
        ),
    )


def test_buy_hold_is_next_bar_and_numerically_reconciled() -> None:
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        initial_cash=Decimal("100000"),
    )
    result = BacktestEngine().run(config, sample_frame())
    assert len(result.orders) == 1
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.timestamp.date().isoformat() == "2026-01-06"
    assert trade.price == Decimal("11.0")
    assert trade.commission == Decimal("5.00")
    assert result.daily_equity[0].total_assets == Decimal("100000")
    assert result.daily_equity[1].cash == Decimal("98895.00")
    assert result.daily_equity[1].positions_value == Decimal("1100.0")
    assert result.daily_equity[1].total_assets == Decimal("99995.00")


def test_order_constraints_t1_cancel_idempotency_and_partial_fill() -> None:
    broker = make_broker(participation="0.5")
    day1 = datetime(2026, 1, 5, tzinfo=UTC)
    broker.set_time(day1)
    broker.mark([make_bar(timestamp=day1)])
    invalid_lot = broker.submit_order(OrderRequest("600519.SH", Direction.BUY, 101))
    assert invalid_lot.status is OrderStatus.RISK_REJECTED
    too_large = broker.submit_order(OrderRequest("600519.SH", Direction.BUY, 20000))
    assert too_large.status is OrderStatus.RISK_REJECTED

    request = OrderRequest("600519.SH", Direction.BUY, 1000, idempotency_key="same-request")
    order = broker.submit_order(request)
    assert broker.submit_order(request) is order
    assert broker.account.frozen_cash > 0
    day2 = datetime(2026, 1, 6, tzinfo=UTC)
    broker.set_time(day2)
    trades = broker.match([make_bar(timestamp=day2, volume=1000)])
    assert trades[0].quantity == 500
    assert order.status is OrderStatus.PARTIALLY_FILLED
    position = broker.account.positions["600519.SH"]
    assert position.available_quantity == 0
    broker.cancel_order(CancelRequest(order.order_id))
    assert order.status is OrderStatus.CANCELLED
    assert broker.account.frozen_cash == 0
    broker.settle_new_day()
    assert position.available_quantity == 500


def test_suspension_limit_boards_and_slippage_direction() -> None:
    day1 = datetime(2026, 1, 5, tzinfo=UTC)
    day2 = datetime(2026, 1, 6, tzinfo=UTC)
    broker = make_broker(slippage="10")
    broker.set_time(day1)
    broker.mark([make_bar(timestamp=day1)])
    order = broker.submit_order(OrderRequest("600519.SH", Direction.BUY, 100))
    broker.set_time(day2)
    assert not broker.match([make_bar(timestamp=day2, suspended=True)])
    assert not broker.match([make_bar(timestamp=day2, open_price="11", limit_up="11")])
    trade = broker.match([make_bar(timestamp=day2, open_price="10")])[0]
    assert trade.price == Decimal("10.010")
    assert order.status is OrderStatus.FILLED


def test_sell_stamp_duty_position_freeze_and_limit_down() -> None:
    fees = AShareCommission().calculate(Direction.SELL, Decimal("10"), 1000)
    assert fees.commission == Decimal("5.00")
    assert fees.stamp_duty == Decimal("5.00")
    broker = make_broker()
    day1 = datetime(2026, 1, 5, tzinfo=UTC)
    broker.set_time(day1)
    broker.mark([make_bar(timestamp=day1)])
    broker.submit_order(OrderRequest("600519.SH", Direction.BUY, 100))
    day2 = datetime(2026, 1, 6, tzinfo=UTC)
    broker.set_time(day2)
    broker.settle_new_day()
    broker.match([make_bar(timestamp=day2)])
    broker.mark([make_bar(timestamp=day2)])
    day3 = datetime(2026, 1, 7, tzinfo=UTC)
    broker.set_time(day3)
    broker.settle_new_day()
    broker.mark([make_bar(timestamp=day3)])
    position = broker.account.positions["600519.SH"]
    assert position.available_quantity == 100
    sell = broker.submit_order(OrderRequest("600519.SH", Direction.SELL, 100))
    assert position.available_quantity == 0
    assert position.frozen_quantity == 100
    day4 = datetime(2026, 1, 8, tzinfo=UTC)
    broker.set_time(day4)
    assert not broker.match([make_bar(timestamp=day4, open_price="9", limit_down="9")])
    broker.cancel_order(CancelRequest(sell.order_id))
    assert position.available_quantity == 100
    assert position.frozen_quantity == 0


def test_insufficient_position_is_rejected() -> None:
    broker = make_broker()
    timestamp = datetime(2026, 1, 5, tzinfo=UTC)
    broker.set_time(timestamp)
    broker.mark([make_bar(timestamp=timestamp)])
    order = broker.submit_order(OrderRequest("600519.SH", Direction.SELL, 100))
    assert order.status is OrderStatus.RISK_REJECTED
    assert order.reject_reason and "INSUFFICIENT_POSITION" in order.reject_reason


def test_repeated_backtest_is_deterministic() -> None:
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        initial_cash=Decimal("100000"),
    )
    first = BacktestEngine().run(config, sample_frame())
    second = BacktestEngine().run(config, sample_frame())
    assert first.data_fingerprint == second.data_fingerprint
    assert first.equity_frame().drop(columns=[]).equals(second.equity_frame())
    assert [(trade.quantity, trade.price) for trade in first.trades] == [
        (trade.quantity, trade.price) for trade in second.trades
    ]


def test_execution_delay_changes_fill_bar_without_same_bar_execution() -> None:
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        initial_cash=Decimal("1000000"),
    )
    next_bar = BacktestEngine().run(config, sample_frame())
    delayed = BacktestEngine().run(
        config.model_copy(update={"execution_delay_bars": 2}), sample_frame()
    )
    assert next_bar.trades[0].timestamp < delayed.trades[0].timestamp
    assert next_bar.trades[0].timestamp == pd.Timestamp("2026-01-06").to_pydatetime()
    assert delayed.trades[0].timestamp == pd.Timestamp("2026-01-07").to_pydatetime()


def test_benchmark_is_point_in_time_aligned_with_strategy_net_value() -> None:
    strategy_bars = sample_frame()
    benchmark = strategy_bars.copy()
    benchmark["symbol"] = "000300.SH"
    benchmark["open"] = [100, 101, 102, 103]
    benchmark["high"] = [101, 102, 103, 104]
    benchmark["low"] = [99, 100, 101, 102]
    benchmark["close"] = [100, 101, 102, 103]
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        benchmark_symbol="000300.SH",
        initial_cash=Decimal("1000000"),
    )
    result = BacktestEngine().run(config, pd.concat([strategy_bars, benchmark]))
    assert result.daily_equity[0].benchmark_net_value == Decimal("1")
    assert result.daily_equity[-1].benchmark_net_value == Decimal("1.03")
    assert result.daily_equity[-1].relative_net_value is not None
    assert "information_ratio" in result.metrics


def test_minute_engine_price_tick_listing_and_st_limit_policy() -> None:
    minute_frame = sample_frame().iloc[:3].copy()
    minute_frame["datetime"] = pd.to_datetime(
        ["2026-01-05 09:30", "2026-01-05 09:31", "2026-01-05 09:32"]
    )
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        interval=Interval.MINUTE_1,
        initial_cash=Decimal("100000"),
        slippage_bps=Decimal("1"),
    )
    minute_result = BacktestEngine().run(config, minute_frame)
    assert minute_result.trades[0].timestamp == datetime(2026, 1, 5, 9, 31)
    assert minute_result.trades[0].price % Decimal("0.01") == 0
    assert minute_result.config_snapshot["interval"] == "1m"

    not_listed = Instrument(
        "600519.SH",
        Exchange.SH,
        InstrumentType.STOCK,
        list_date=date(2026, 2, 1),
    )
    broker = SimulatedBroker(
        initial_cash=Decimal("100000"),
        commission=AShareCommission(),
        matcher=BarMatcher(),
        instruments={"600519.SH": not_listed},
    )
    at = datetime(2026, 1, 5, tzinfo=UTC)
    broker.set_time(at)
    broker.mark([make_bar(timestamp=at)])
    rejected_listing = broker.submit_order(OrderRequest("600519.SH", Direction.BUY, 100))
    assert rejected_listing.reject_reason and "NOT_LISTED" in rejected_listing.reject_reason

    listed_st = Instrument(
        "600519.SH",
        Exchange.SH,
        InstrumentType.STOCK,
        is_st=True,
    )
    st_broker = SimulatedBroker(
        initial_cash=Decimal("100000"),
        commission=AShareCommission(),
        matcher=BarMatcher(),
        instruments={"600519.SH": listed_st},
    )
    st_broker.set_time(at)
    st_broker.mark([make_bar(timestamp=at)])
    invalid_tick = st_broker.submit_order(
        OrderRequest(
            "600519.SH",
            Direction.BUY,
            100,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("10.005"),
        )
    )
    assert invalid_tick.reject_reason and "INVALID_PRICE_TICK" in invalid_tick.reject_reason
    order = st_broker.submit_order(OrderRequest("600519.SH", Direction.BUY, 100))
    next_day = datetime(2026, 1, 6, tzinfo=UTC)
    st_bar = Bar(
        "600519.SH",
        next_day,
        Interval.DAY_1,
        Decimal("10.5"),
        Decimal("10.5"),
        Decimal("10.5"),
        Decimal("10.5"),
        10000,
        pre_close=Decimal("10"),
    )
    st_broker.set_time(next_day)
    assert not st_broker.match([st_bar])
    assert order.status is OrderStatus.SUBMITTED


@given(
    st.decimals(min_value="0.01", max_value="1000", places=2),
    st.integers(min_value=1, max_value=100000),
    st.sampled_from([Direction.BUY, Direction.SELL]),
)
def test_fees_are_never_negative(price: Decimal, quantity: int, direction: Direction) -> None:
    fees = AShareCommission().calculate(direction, price, quantity)
    assert fees.commission >= 0
    assert fees.stamp_duty >= 0
    assert fees.transfer_fee >= 0
    assert fees.total >= 0


def test_missing_data_and_duplicate_data_fail_closed() -> None:
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
    )
    with pytest.raises(ValueError, match="no data"):
        BacktestEngine().run(config, sample_frame().assign(symbol="000001.SZ"))
    duplicated = pd.concat([sample_frame(), sample_frame().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        BacktestEngine().run(config, duplicated)
