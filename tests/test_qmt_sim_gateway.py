from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from dyadtetrade.core.config import ExecutionMode, Settings
from dyadtetrade.domain import Direction, EventType, OrderRequest, OrderStatus, Tick
from dyadtetrade.integrations.qmt_sim_gateway import (
    QmtOrderConstants,
    QmtSimTradingGateway,
    SingleProcessLock,
)


class FakeQmtClient:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.submissions: list[tuple[object, ...]] = []
        self.cancels: list[int] = []
        self.orders: list[object] = []
        self.orders_by_id: dict[int, object] = {}
        self.trades: list[object] = []
        self.positions: list[object] | None = []
        self.asset: object | None = {
            "cash": 100_000,
            "frozen_cash": 0,
            "total_asset": 100_000,
        }
        self.asset_query_count = 0
        self.positions_query_count = 0
        self.cancel_hook: Callable[[], None] | None = None

    def start(self) -> None:
        self.started = True

    def connect(self) -> int:
        return 0

    def subscribe(self, account: object) -> int:
        return 0

    def stop(self) -> None:
        self.stopped = True

    def query_stock_asset(self, account: object) -> object | None:
        self.asset_query_count += 1
        return self.asset

    def query_stock_positions(self, account: object) -> list[object] | None:
        self.positions_query_count += 1
        return self.positions

    def query_stock_orders(self, account: object, cancelable_only: bool = False) -> list[object]:
        return self.orders

    def query_stock_order(self, account: object, order_id: int) -> object | None:
        return self.orders_by_id.get(order_id)

    def query_stock_trades(self, account: object) -> list[object]:
        return self.trades

    def order_stock_async(self, *args: object, **kwargs: object) -> int:
        self.submissions.append((*args, kwargs))
        return 7

    def cancel_order_stock(self, account: object, order_id: int) -> int:
        self.cancels.append(order_id)
        if self.cancel_hook is not None:
            self.cancel_hook()
        return 0


TEST_QMT_ORDER_CONSTANTS = QmtOrderConstants(
    stock_buy=23,
    stock_sell=24,
    fix_price=11,
    latest_price=5,
)


def settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "execution_mode": ExecutionMode.QMT_SIM,
        "enable_qmt_sim_trading": True,
        "enable_live_trading": False,
        "qmt_account_id": "SIMULATION-ACCOUNT",
        "qmt_userdata_path": tmp_path,
        "allowed_symbols": ["600519.SH"],
        "max_order_notional": Decimal("10000"),
        "max_daily_notional": Decimal("50000"),
        "max_orders_per_minute": 2,
        "max_symbol_position_notional": Decimal("20000"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def tick(at: datetime | None = None) -> Tick:
    return Tick(
        symbol="600519.SH",
        timestamp=at or datetime.now(UTC),
        last_price=Decimal("10"),
        volume=1000,
        amount=Decimal("10000"),
    )


def request(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "symbol": "600519.SH",
        "direction": Direction.BUY,
        "quantity": 100,
        "limit_price": Decimal("10"),
        "account_id": "SIMULATION-ACCOUNT",
        "strategy_id": "manual",
        "client_order_id": "client-1",
        "idempotency_key": "key-1",
    }
    values.update(overrides)
    return OrderRequest(**values)


def make_gateway(tmp_path: Path, **overrides: Any) -> tuple[QmtSimTradingGateway, FakeQmtClient]:
    client = FakeQmtClient()
    gateway = QmtSimTradingGateway(
        settings=settings(tmp_path, **overrides),
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "gateway.lock"),
    )
    gateway.start()
    gateway.update_market(tick())
    return gateway, client


def test_execution_modes_and_live_mode_fail_closed(tmp_path: Path) -> None:
    Settings(_env_file=None).validate_execution_safety()
    with pytest.raises(RuntimeError, match="qmt_live"):
        Settings(_env_file=None, execution_mode="qmt_live").validate_execution_safety()
    with pytest.raises(RuntimeError, match="QMT_ACCOUNT_ID"):
        Settings(
            _env_file=None,
            execution_mode="qmt_sim",
            enable_qmt_sim_trading=True,
            qmt_userdata_path=tmp_path,
            allowed_symbols=["600519.SH"],
        ).validate_execution_safety()


def test_restored_daily_usage_blocks_restart_limit_bypass(tmp_path: Path) -> None:
    gateway, _ = make_gateway(tmp_path, max_daily_notional=Decimal("1000"))
    now = datetime.now(UTC)
    gateway.safety.restore_daily_usage(
        trading_day=now.astimezone(ZoneInfo("Asia/Shanghai")).date(),
        notional=Decimal("950"),
        idempotency_keys={"already-used"},
        order_timestamps=[now],
    )
    duplicate = request(idempotency_key="already-used")
    duplicate_result = gateway.safety.check(
        duplicate,
        account=gateway.account,
        tick=gateway.market["600519.SH"],
        connected=True,
        subscribed=True,
        account_healthy=True,
        now=now,
    )
    assert duplicate_result.code == "DUPLICATE_ORDER"
    daily_limit = request(idempotency_key="new-after-restart")
    daily_result = gateway.safety.check(
        daily_limit,
        account=gateway.account,
        tick=gateway.market["600519.SH"],
        connected=True,
        subscribed=True,
        account_healthy=True,
        now=now,
    )
    assert daily_result.code == "MAX_DAILY_NOTIONAL"
    first = SingleProcessLock(tmp_path / "single.lock")
    second = SingleProcessLock(tmp_path / "single.lock")
    first.acquire()
    try:
        if os.name == "nt":
            with pytest.raises(RuntimeError, match="another QMT"):
                second.acquire()
    finally:
        first.release()


def test_qmt_sim_async_response_callbacks_cancel_and_reconcile(tmp_path: Path) -> None:
    gateway, client = make_gateway(tmp_path)
    submission = gateway.submit_async(request())
    assert submission.mapping.qmt_seq == 7
    assert len(client.submissions) == 1

    gateway.on_async_order_response({"seq": 7, "order_id": 9001, "error_msg": ""})
    assert submission.mapping.qmt_order_id == 9001
    assert gateway.trades == {}
    gateway.on_order(
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "order_sysid": "SYS-1",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 0,
            "order_status": 48,
            "order_remark": "client-1",
        }
    )
    assert gateway.orders["9001"].request.account_id == "masked"
    assert gateway.trades == {}
    assert gateway.cancel(9001) == 0
    assert client.cancels == [9001]
    gateway.on_trade(
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "traded_id": "trade-1",
            "order_type": 23,
            "traded_volume": 100,
            "traded_price": 10,
            "traded_time": 1_767_225_600,
        }
    )
    assert "trade-1" in gateway.trades
    assert gateway.orders["9001"].status is OrderStatus.FILLED
    assert gateway.reconcile()["account"] is True
    gateway.stop()
    assert client.stopped is True
    assert gateway.event_bus._worker_thread is None


def test_pretrade_risk_refreshes_broker_cash_and_positions(tmp_path: Path) -> None:
    gateway, client = make_gateway(tmp_path)
    asset_queries = client.asset_query_count
    positions_queries = client.positions_query_count
    client.asset = {
        "cash": 500,
        "frozen_cash": 100,
        "market_value": 0,
        "total_asset": 100_000,
    }

    with pytest.raises(PermissionError, match="INSUFFICIENT_CASH"):
        gateway.submit_async(request())

    assert client.asset_query_count == asset_queries + 1
    assert client.positions_query_count == positions_queries + 1
    assert gateway.account is not None
    assert gateway.account.available_cash == Decimal("500")
    assert gateway.account.cash == Decimal("600")
    assert gateway.status()["account_data_source"] == ("query_stock_asset/query_stock_positions")
    gateway.stop()


def test_pretrade_query_failure_fails_closed_without_submitting(tmp_path: Path) -> None:
    gateway, client = make_gateway(tmp_path)
    client.positions = None

    with pytest.raises(PermissionError, match="ACCOUNT_UNHEALTHY"):
        gateway.submit_async(request())

    assert client.submissions == []
    assert gateway.account_healthy is False
    assert gateway.last_error == "QMT positions query failed"
    gateway.stop()


def test_pretrade_risk_uses_fresh_broker_sellable_position(tmp_path: Path) -> None:
    gateway, client = make_gateway(tmp_path)
    gateway.on_position(
        {
            "stock_code": "600519.SH",
            "volume": 100,
            "can_use_volume": 100,
            "market_value": 1000,
        }
    )
    client.positions = [
        {
            "stock_code": "600519.SH",
            "volume": 100,
            "can_use_volume": 0,
            "frozen_volume": 100,
            "market_value": 1000,
        }
    ]

    with pytest.raises(PermissionError, match="INSUFFICIENT_POSITION"):
        gateway.submit_async(request(direction=Direction.SELL))

    assert client.submissions == []
    assert gateway.positions["600519.SH"].available_quantity == 0
    assert gateway.positions["600519.SH"].frozen_quantity == 100
    gateway.stop()


def test_qmt_sim_safety_rejects_account_duplicate_notional_stale_disconnect_and_kill(
    tmp_path: Path,
) -> None:
    gateway, _ = make_gateway(tmp_path)
    with pytest.raises(PermissionError, match="ACCOUNT_NOT_ALLOWED"):
        gateway.submit_async(request(account_id="not-allowed"))
    with pytest.raises(PermissionError, match="MAX_ORDER_NOTIONAL"):
        gateway.submit_async(request(quantity=1100, idempotency_key="large"))
    gateway.update_market(tick(datetime.now(UTC) - timedelta(minutes=1)))
    with pytest.raises(PermissionError, match="STALE_MARKET"):
        gateway.submit_async(request(idempotency_key="stale"))
    gateway.update_market(tick())
    gateway.submit_async(request())
    with pytest.raises(PermissionError, match="DUPLICATE_ORDER"):
        gateway.submit_async(request())
    gateway.connected = False
    with pytest.raises(PermissionError, match="GATEWAY_DISCONNECTED"):
        gateway.submit_async(request(idempotency_key="disconnect"))
    gateway.connected = True
    gateway.safety.switches.global_stop = True
    with pytest.raises(PermissionError, match="KILL_SWITCH"):
        gateway.submit_async(request(idempotency_key="kill"))
    gateway.stop()


def test_qmt_safety_enforces_portfolio_daily_order_and_strategy_limits(
    tmp_path: Path,
) -> None:
    total_limited, _ = make_gateway(
        tmp_path,
        max_total_position_notional=Decimal("500"),
    )
    with pytest.raises(PermissionError, match="MAX_TOTAL_POSITION"):
        total_limited.submit_async(request())
    total_limited.stop()

    ratio_limited, _ = make_gateway(
        tmp_path,
        max_total_position_ratio=Decimal("0.005"),
    )
    with pytest.raises(PermissionError, match="MAX_TOTAL_POSITION_RATIO"):
        ratio_limited.submit_async(request())
    ratio_limited.stop()

    daily_limited, _ = make_gateway(tmp_path, max_daily_orders=1)
    daily_limited.submit_async(request())
    with pytest.raises(PermissionError, match="MAX_DAILY_ORDERS"):
        daily_limited.submit_async(request(client_order_id="client-2", idempotency_key="key-2"))
    daily_limited.stop()

    strategy_limited, _ = make_gateway(
        tmp_path,
        max_strategy_daily_notional=Decimal("1000"),
    )
    strategy_limited.submit_async(request())
    with pytest.raises(PermissionError, match="MAX_STRATEGY_DAILY_NOTIONAL"):
        strategy_limited.submit_async(request(client_order_id="client-3", idempotency_key="key-3"))
    strategy_limited.stop()


def test_qmt_sim_disconnect_reconnect_account_position_and_error_callbacks(tmp_path: Path) -> None:
    gateway, _ = make_gateway(tmp_path)
    gateway.on_account_status({"status": 1})
    assert gateway.account_healthy is False
    assert gateway.recovery_required is True
    gateway.on_asset({"cash": 10000, "frozen_cash": 100})
    gateway.on_position(
        {
            "stock_code": "600519.SH",
            "volume": 100,
            "can_use_volume": 0,
            "open_price": 10,
            "market_value": 1100,
        }
    )
    assert gateway.positions["600519.SH"].quantity == 100
    gateway.on_order_error({"seq": 999, "error_msg": "rejected"})
    gateway.on_disconnected()
    assert gateway.connected is False and gateway.safety.switches.cancel_only is True
    assert gateway.recovery_required is True
    assert gateway.reconnect(max_attempts=1) is True
    assert gateway.connected is True and gateway.subscribed is True
    assert gateway.recovery_required is True
    assert gateway.safety.switches.cancel_only is True
    gateway.stop()


def _order_callback(status: int, *, traded_volume: int = 0) -> dict[str, object]:
    return {
        "stock_code": "600519.SH",
        "order_id": 9001,
        "order_sysid": "SYS-1",
        "order_type": 23,
        "order_volume": 100,
        "traded_volume": traded_volume,
        "traded_price": 10 if traded_volume else 0,
        "order_status": status,
        "order_remark": "client-1",
    }


def test_duplicate_and_out_of_order_callbacks_are_idempotent(tmp_path: Path) -> None:
    gateway, _ = make_gateway(tmp_path)
    gateway.submit_async(
        request(
            strategy_release_id="release-lineage",
            signal_id="signal-lineage",
        )
    )

    # The order callback may arrive before the asynchronous sequence response.
    gateway.on_order(_order_callback(48))
    assert gateway.mappings_by_order_id[9001].client_order_id == "client-1"
    gateway.on_async_order_response({"seq": 7, "order_id": 9001, "error_msg": ""})
    assert gateway.mappings_by_seq[7].qmt_order_id == 9001

    gateway.event_bus.wait_until_idle()
    order_events_before_duplicate = len(
        [event for event in gateway.event_bus.history if event.event_type is EventType.ORDER]
    )
    gateway.on_order(_order_callback(48))
    gateway.event_bus.wait_until_idle()
    order_events_after_duplicate = len(
        [event for event in gateway.event_bus.history if event.event_type is EventType.ORDER]
    )
    assert order_events_after_duplicate == order_events_before_duplicate
    assert gateway.orders["9001"].request.strategy_release_id == "release-lineage"
    assert gateway.orders["9001"].request.signal_id == "signal-lineage"

    gateway.on_order(_order_callback(56, traded_volume=100))
    gateway.on_order(_order_callback(54, traded_volume=0))
    assert gateway.orders["9001"].status is OrderStatus.FILLED
    assert gateway.orders["9001"].filled_quantity == 100

    trade = {
        "stock_code": "600519.SH",
        "order_id": 9001,
        "traded_id": "trade-idempotent",
        "order_type": 23,
        "traded_volume": 100,
        "traded_price": 10,
        "traded_time": 1_767_225_600,
    }
    gateway.on_trade(trade)
    gateway.on_trade(trade)
    gateway.event_bus.wait_until_idle()
    trade_events = [
        event
        for event in gateway.event_bus.history
        if event.event_type is EventType.TRADE
        and getattr(event.payload, "trade_id", "") == "trade-idempotent"
    ]
    assert len(trade_events) == 1
    gateway.stop()


def test_fill_wins_cancel_race_regardless_of_callback_order(tmp_path: Path) -> None:
    gateway, _ = make_gateway(tmp_path)
    gateway.on_order(_order_callback(54))
    assert gateway.orders["9001"].status is OrderStatus.CANCELLED
    gateway.on_order(_order_callback(56, traded_volume=100))
    assert gateway.orders["9001"].status is OrderStatus.FILLED
    gateway.on_order(_order_callback(48))
    assert gateway.orders["9001"].status is OrderStatus.FILLED
    gateway.stop()


def test_trade_before_order_callback_advances_pending_order_without_regression(
    tmp_path: Path,
) -> None:
    gateway, _ = make_gateway(tmp_path)
    gateway.submit_async(request())
    gateway.on_async_order_response({"seq": 7, "order_id": 9001, "error_msg": ""})

    gateway.on_trade(
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "order_sysid": "SYS-1",
            "traded_id": "trade-before-order",
            "order_type": 23,
            "traded_volume": 40,
            "traded_price": 10,
            "traded_time": 1_767_225_600,
        }
    )
    pending = gateway.orders["qmt-seq-7"]
    assert pending.status is OrderStatus.PARTIALLY_FILLED
    assert pending.filled_quantity == 40

    gateway.on_order(_order_callback(50, traded_volume=0))
    recovered = gateway.orders["9001"]
    assert recovered.status is OrderStatus.PARTIALLY_FILLED
    assert recovered.filled_quantity == 40
    gateway.stop()


def test_cancel_request_and_partial_fill_race_are_monotonic(tmp_path: Path) -> None:
    gateway, _ = make_gateway(tmp_path)
    gateway.on_order(_order_callback(55, traded_volume=40))

    assert gateway.cancel(9001) == 0
    assert gateway.orders["9001"].status is OrderStatus.CANCELLING

    gateway.on_order(_order_callback(55, traded_volume=60))
    assert gateway.orders["9001"].status is OrderStatus.CANCELLING
    assert gateway.orders["9001"].filled_quantity == 60

    gateway.on_order(_order_callback(54, traded_volume=60))
    assert gateway.orders["9001"].status is OrderStatus.CANCELLED
    assert gateway.orders["9001"].filled_quantity == 60
    gateway.stop()


def test_fill_callback_during_synchronous_cancel_response_remains_terminal(
    tmp_path: Path,
) -> None:
    gateway, client = make_gateway(tmp_path)
    gateway.on_order(_order_callback(50))
    client.cancel_hook = lambda: gateway.on_order(_order_callback(56, traded_volume=100))

    assert gateway.cancel(9001) == 0
    assert gateway.orders["9001"].status is OrderStatus.FILLED
    assert gateway.orders["9001"].filled_quantity == 100
    with pytest.raises(RuntimeError, match="cannot cancel final order"):
        gateway.cancel(9001)
    assert client.cancels == [9001]
    gateway.stop()


def test_cancel_error_restores_active_order_and_records_reason(tmp_path: Path) -> None:
    gateway, _ = make_gateway(tmp_path)
    gateway.submit_async(request())
    gateway.on_async_order_response({"seq": 7, "order_id": 9001, "error_msg": ""})
    gateway.on_order(_order_callback(50))
    assert gateway.cancel(9001) == 0

    gateway.on_cancel_error(
        {
            "order_id": 9001,
            "order_sysid": "SYS-1",
            "error_id": -1,
            "error_msg": "cancel rejected",
        }
    )

    assert gateway.orders["9001"].status is OrderStatus.SUBMITTED
    assert gateway.mappings_by_order_id[9001].last_error == "cancel rejected"
    assert gateway.last_error == "QMT cancel failed"
    gateway.stop()


def test_order_error_uses_broker_identity_when_sequence_is_absent(tmp_path: Path) -> None:
    gateway, _ = make_gateway(tmp_path)
    gateway.submit_async(request())
    gateway.on_async_order_response({"seq": 7, "order_id": 9001, "error_msg": ""})
    gateway.on_order(_order_callback(50))

    gateway.on_order_error(
        {
            "order_id": 9001,
            "order_remark": "client-1",
            "error_id": 1001,
            "error_msg": "broker rejected order",
        }
    )

    assert gateway.orders["9001"].status is OrderStatus.REJECTED
    assert gateway.mappings_by_order_id[9001].last_error == "broker rejected order"
    gateway.stop()


def test_reconciliation_query_cannot_regress_terminal_order(tmp_path: Path) -> None:
    gateway, client = make_gateway(tmp_path)
    gateway.on_order(_order_callback(56, traded_volume=100))
    client.orders = [_order_callback(50, traded_volume=0)]

    gateway.reconcile()

    assert gateway.orders["9001"].status is OrderStatus.FILLED
    assert gateway.orders["9001"].filled_quantity == 100
    gateway.stop()
