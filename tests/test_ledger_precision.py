from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from dyadtetrade.domain import (
    Direction,
    Order,
    OrderIntent,
    OrderRequest,
    OrderStatus,
    Signal,
    Trade,
)
from dyadtetrade.domain.precision import quantize_money, quantize_price
from dyadtetrade.modules.execution.service import validate_broker_trade
from dyadtetrade.modules.oms import OrderManagementService
from dyadtetrade.modules.paper_trading import PaperTradingService


def test_signal_and_order_intent_do_not_create_orders_or_trades() -> None:
    now = datetime.now(UTC)
    oms = OrderManagementService()
    signal = Signal("strategy", "600519.SH", now, Direction.BUY)
    request = OrderRequest("600519.SH", Direction.BUY, 100)
    intent = OrderIntent("intent-1", request, now)
    assert signal.symbol == intent.request.symbol
    assert oms.orders == {}
    assert oms.trades == {}


def test_trade_recording_is_idempotent() -> None:
    oms = OrderManagementService()
    trade = Trade(
        "broker-trade-1",
        "broker-order-1",
        "600519.SH",
        Direction.BUY,
        100,
        Decimal("10.01"),
        datetime.now(UTC),
        execution_mode="qmt_sim",
        account_id="sim",
        source="qmt_trade_callback",
        broker_order_id="broker-order-1",
        order_sysid="broker-order-1",
        broker_trade_id="broker-trade-1",
        broker_reported_at=datetime.now(UTC),
        fee_breakdown_complete=False,
    )
    oms.record_trade(trade)
    oms.record_trade(trade)
    assert list(oms.trades) == ["broker-trade-1"]
    validate_broker_trade(trade)


def test_paper_trade_is_rejected_by_qmt_broker_ledger_guard() -> None:
    trade = Trade(
        "paper-trade",
        "paper-order",
        "600519.SH",
        Direction.BUY,
        100,
        Decimal("10"),
        datetime.now(UTC),
        execution_mode="paper",
        account_id="paper",
        source="paper_match",
    )
    with pytest.raises(ValueError, match="only accepts QMT trade facts"):
        validate_broker_trade(trade)


def test_qmt_trade_rejects_zero_broker_order_identity() -> None:
    trade = Trade(
        "broker-trade-zero-order",
        "0",
        "600519.SH",
        Direction.BUY,
        100,
        Decimal("10"),
        datetime.now(UTC),
        execution_mode="qmt_sim",
        account_id="sim",
        source="qmt_trade_query",
        broker_order_id="0",
        order_sysid="0",
        broker_trade_id="broker-trade-zero-order",
        broker_reported_at=datetime.now(UTC),
        fee_breakdown_complete=False,
    )
    with pytest.raises(ValueError, match="missing mandatory broker identity"):
        validate_broker_trade(trade)


@pytest.mark.parametrize(
    ("value", "tick", "expected"),
    [("10.01", "0.01", "10.01"), ("1.234", "0.001", "1.234")],
)
def test_price_tick_quantization(value: str, tick: str, expected: str) -> None:
    assert str(quantize_price(value, Decimal(tick))) == expected


def test_price_not_on_tick_is_rejected() -> None:
    with pytest.raises(ValueError, match="integer multiple"):
        quantize_price("10.005", Decimal("0.01"))


def test_partial_fill_weighted_average_preserves_decimal_precision() -> None:
    now = datetime.now(UTC)
    order = Order("order", OrderRequest("600519.SH", Direction.BUY, 200))
    order.transition(OrderStatus.SUBMITTING, now)
    order.transition(OrderStatus.SUBMITTED, now)
    order.apply_fill(100, Decimal("10.01"), now)
    order.apply_fill(100, Decimal("10.02"), now)
    assert order.average_fill_price == Decimal("10.015")
    assert quantize_money(order.average_fill_price * 200) == Decimal("2003.00")


def test_paper_snapshot_serializes_accounting_decimals_as_strings() -> None:
    snapshot = PaperTradingService(initial_cash=Decimal("1000000.12345678")).snapshot()
    account = snapshot["account"]
    assert isinstance(account, dict)
    assert account["cash"] == "1000000.12345678"
