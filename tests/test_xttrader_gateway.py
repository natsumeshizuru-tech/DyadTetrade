from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from dyadtetrade.domain import EventType
from dyadtetrade.events import EventBus
from dyadtetrade.integrations.xttrader_gateway import GatewayState, XtTraderGatewaySkeleton


class MockReadOnlyClient:
    def __init__(self) -> None:
        self.connect_count = 0
        self.disconnected = False

    def connect(self) -> bool:
        self.connect_count += 1
        return True

    def disconnect(self) -> None:
        self.disconnected = True

    def query_account(self) -> dict[str, Any]:
        return {"account_id": "secret-account", "cash": 100000, "frozen_cash": 1000}

    def query_positions(self) -> list[dict[str, Any]]:
        return [
            {
                "stock_code": "510300.SH",
                "volume": 1000,
                "can_use_volume": 500,
                "open_price": 4,
                "market_value": 4200,
            }
        ]

    def query_orders(self) -> list[dict[str, Any]]:
        return [
            {
                "stock_code": "510300.SH",
                "order_id": 1,
                "order_type": 23,
                "order_volume": 100,
                "traded_volume": 0,
                "order_status": 48,
            }
        ]

    def query_trades(self) -> list[dict[str, Any]]:
        return [
            {
                "stock_code": "510300.SH",
                "order_id": 1,
                "traded_id": 2,
                "order_type": 23,
                "traded_volume": 100,
                "traded_price": 4.2,
                "traded_time": 1_767_225_600,
            }
        ]


def test_read_only_gateway_connect_reconcile_and_reconnect_without_sensitive_account() -> None:
    client = MockReadOnlyClient()
    gateway = XtTraderGatewaySkeleton(environment="dev", client=client)
    assert gateway.connect() is True
    snapshot = gateway.reconcile()
    assert snapshot["account_available"] is True
    assert snapshot["position_count"] == 1
    assert snapshot["order_count"] == 1
    assert snapshot["trade_count"] == 1
    account = gateway.query_account()
    assert account is not None and account.account_id == "masked"
    stale_at = datetime.now(UTC) + timedelta(minutes=2)
    assert gateway.check_connection(now=stale_at, max_age=timedelta(seconds=1)) is False
    assert gateway.state is GatewayState.DEGRADED
    assert gateway.reconnect(max_attempts=1) is True
    assert gateway.state is GatewayState.CONNECTED
    assert client.connect_count == 2
    gateway.event_bus.stop_worker()


def test_vendor_callback_only_converts_and_queues_event_off_callback_thread() -> None:
    event_bus = EventBus()
    handler_threads: list[str] = []
    event_bus.subscribe(
        EventType.ORDER,
        lambda event: handler_threads.append(threading.current_thread().name),
    )
    gateway = XtTraderGatewaySkeleton(event_bus=event_bus)
    callback_thread = threading.current_thread().name
    gateway.on_order_callback(
        {
            "stock_code": "510300.SH",
            "order_id": 100,
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 0,
            "order_status": 48,
        }
    )
    event_bus.wait_until_idle()
    assert handler_threads == ["dyadtetrade-event-worker"]
    assert handler_threads[0] != callback_thread
    assert gateway.query_orders()[0].request.account_id == "masked"
    event_bus.stop_worker()
