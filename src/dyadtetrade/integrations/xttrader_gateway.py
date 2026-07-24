from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

from dyadtetrade.domain import Account, EventType, Order, OrderRequest, Position, Trade
from dyadtetrade.events import Event, EventBus
from dyadtetrade.integrations.xtquant_converters import (
    account_from_xttrader,
    order_from_xttrader,
    position_from_xttrader,
    trade_from_xttrader,
)


class LiveTradingDisabledError(PermissionError):
    pass


class GatewayState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"


class TradingGateway(Protocol):
    def connection_status(self) -> dict[str, object]: ...

    def query_account(self) -> Account | None: ...

    def query_positions(self) -> list[Position]: ...

    def query_orders(self) -> list[Order]: ...

    def query_trades(self) -> list[Trade]: ...

    def submit_order(self, request: OrderRequest) -> Order: ...

    def close(self) -> None: ...


class XtTraderReadOnlyClient(Protocol):
    """Minimal injectable vendor boundary; implementations may only query."""

    def connect(self) -> bool: ...

    def disconnect(self) -> None: ...

    def query_account(self) -> dict[str, Any] | None: ...

    def query_positions(self) -> list[dict[str, Any]]: ...

    def query_orders(self) -> list[dict[str, Any]]: ...

    def query_trades(self) -> list[dict[str, Any]]: ...


class XtTraderGatewaySkeleton:
    """Read-only gateway state machine with callback isolation and reconciliation.

    This object deliberately contains no vendor order/cancel/algo method. Raw
    callbacks are converted and published only; strategy execution cannot run
    on the callback thread unless a consumer violates the EventBus boundary.
    """

    def __init__(
        self,
        environment: Literal["mock", "paper", "dev", "prod"] = "mock",
        connected: bool = False,
        *,
        client: XtTraderReadOnlyClient | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        if environment not in {"mock", "paper", "dev", "prod"}:
            raise ValueError(f"unsupported gateway environment: {environment}")
        self.environment = environment
        self.client = client
        self.event_bus = event_bus or EventBus()
        self.state = GatewayState.CONNECTED if connected else GatewayState.DISCONNECTED
        self.last_heartbeat: datetime | None = datetime.now(UTC) if connected else None
        self.last_disconnect_reason: str | None = None
        self.reconnect_attempts = 0
        self._lock = RLock()
        self._account: Account | None = None
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._trades: dict[str, Trade] = {}
        self.event_bus.start_worker()

    @property
    def connected(self) -> bool:
        return self.state is GatewayState.CONNECTED

    def connect(self) -> bool:
        self.event_bus.start_worker()
        with self._lock:
            self.state = GatewayState.CONNECTING
        connected = self.client.connect() if self.client is not None else self.environment == "mock"
        with self._lock:
            self.state = GatewayState.CONNECTED if connected else GatewayState.DISCONNECTED
            self.last_heartbeat = datetime.now(UTC) if connected else None
        self._publish_status("connected" if connected else "connection_failed")
        return connected

    def disconnect(self, reason: str = "requested") -> None:
        if self.client is not None:
            self.client.disconnect()
        with self._lock:
            self.state = GatewayState.DISCONNECTED
            self.last_disconnect_reason = reason
        self._publish_status(reason)

    def heartbeat(self, timestamp: datetime | None = None) -> None:
        with self._lock:
            self.last_heartbeat = timestamp or datetime.now(UTC)
            if self.state is GatewayState.DEGRADED:
                self.state = GatewayState.CONNECTED

    def check_connection(
        self, *, now: datetime | None = None, max_age: timedelta = timedelta(seconds=30)
    ) -> bool:
        current = now or datetime.now(UTC)
        with self._lock:
            fresh = (
                self.state is GatewayState.CONNECTED
                and self.last_heartbeat is not None
                and current - self.last_heartbeat <= max_age
            )
            if not fresh and self.state is GatewayState.CONNECTED:
                self.state = GatewayState.DEGRADED
                self.last_disconnect_reason = "heartbeat_stale"
        if not fresh:
            self._publish_status("heartbeat_stale")
        return fresh

    def reconnect(self, *, max_attempts: int = 3) -> bool:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        for _ in range(max_attempts):
            with self._lock:
                self.state = GatewayState.RECONNECTING
                self.reconnect_attempts += 1
            if self.connect():
                self.reconcile()
                return True
        return False

    def connection_status(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "state": self.state.value,
            "connected": self.connected,
            "read_only": True,
            "live_order_capability": False,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "last_disconnect_reason": self.last_disconnect_reason,
            "reconnect_attempts": self.reconnect_attempts,
            "event_bus": self.event_bus.snapshot(),
        }

    def close(self) -> None:
        """Release the vendor connection and the callback-dispatch worker."""
        try:
            self.disconnect("closed")
        finally:
            self.event_bus.close()

    def query_account(self) -> Account | None:
        if self.client is not None and self.connected:
            payload = self.client.query_account()
            if payload is not None:
                self._account = account_from_xttrader(payload)
        return self._account

    def query_positions(self) -> list[Position]:
        if self.client is not None and self.connected:
            converted = [position_from_xttrader(item) for item in self.client.query_positions()]
            self._positions = {item.symbol: item for item in converted}
        return list(self._positions.values())

    def query_orders(self) -> list[Order]:
        if self.client is not None and self.connected:
            converted = [order_from_xttrader(item) for item in self.client.query_orders()]
            self._orders = {item.order_id: item for item in converted}
        return list(self._orders.values())

    def query_trades(self) -> list[Trade]:
        if self.client is not None and self.connected:
            converted = [
                trade_from_xttrader(
                    item,
                    source="qmt_trade_query",
                    execution_mode="disabled",
                    account_id="masked",
                )
                for item in self.client.query_trades()
            ]
            self._trades = {item.trade_id: item for item in converted}
        return list(self._trades.values())

    def reconcile(self) -> dict[str, object]:
        account = self.query_account()
        positions = self.query_positions()
        orders = self.query_orders()
        trades = self.query_trades()
        snapshot = {
            "account_available": account is not None,
            "position_count": len(positions),
            "order_count": len(orders),
            "trade_count": len(trades),
            "reconciled_at": datetime.now(UTC).isoformat(),
        }
        self.event_bus.publish(Event(EventType.ACCOUNT, snapshot, source="xttrader-readonly"))
        return snapshot

    def on_account_callback(self, payload: dict[str, Any]) -> None:
        account = account_from_xttrader(payload)
        with self._lock:
            self._account = account
        self.event_bus.enqueue(Event(EventType.ACCOUNT, account, source="xttrader-callback"))

    def on_position_callback(self, payload: dict[str, Any]) -> None:
        position = position_from_xttrader(payload)
        with self._lock:
            self._positions[position.symbol] = position
        self.event_bus.enqueue(Event(EventType.POSITION, position, source="xttrader-callback"))

    def on_order_callback(self, payload: dict[str, Any]) -> None:
        order = order_from_xttrader(payload)
        with self._lock:
            self._orders[order.order_id] = order
        self.event_bus.enqueue(Event(EventType.ORDER, order, source="xttrader-callback"))

    def on_trade_callback(self, payload: dict[str, Any]) -> None:
        trade = trade_from_xttrader(
            payload,
            source="qmt_trade_callback",
            execution_mode="disabled",
            account_id="masked",
        )
        with self._lock:
            self._trades[trade.trade_id] = trade
        self.event_bus.enqueue(Event(EventType.TRADE, trade, source="xttrader-callback"))

    def submit_order(self, request: OrderRequest) -> Order:
        del request
        raise LiveTradingDisabledError(
            "XtTrader order submission is disabled; use PaperTradingService"
        )

    def _publish_status(self, reason: str) -> None:
        self.event_bus.publish(
            Event(
                EventType.GATEWAY,
                {"gateway": self.connection_status(), "reason": reason},
                source="xttrader-readonly",
            )
        )
