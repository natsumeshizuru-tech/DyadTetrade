from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from random import SystemRandom
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from dyadtetrade.core.config import ExecutionMode, Settings, get_settings
from dyadtetrade.domain import (
    Account,
    Direction,
    EventType,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    RiskResult,
    Tick,
    Trade,
)
from dyadtetrade.events import Event, EventBus
from dyadtetrade.integrations.xtquant_converters import (
    account_from_xttrader,
    object_payload,
    order_from_xttrader,
    position_from_xttrader,
    trade_from_xttrader,
)
from dyadtetrade.modules.risk import TradingSwitches

logger = logging.getLogger(__name__)
CHINA_TZ = ZoneInfo("Asia/Shanghai")


class QmtClient(Protocol):
    def start(self) -> None: ...
    def connect(self) -> int: ...
    def subscribe(self, account: object) -> int: ...
    def stop(self) -> None: ...
    def query_stock_asset(self, account: object) -> object | None: ...
    def query_stock_positions(self, account: object) -> list[object] | None: ...
    def query_stock_order(self, account: object, order_id: int) -> object | None: ...
    def query_stock_orders(
        self, account: object, cancelable_only: bool = False
    ) -> list[object] | None: ...
    def query_stock_trades(self, account: object) -> list[object] | None: ...
    def order_stock_async(
        self,
        account: object,
        stock_code: str,
        order_type: int,
        order_volume: int,
        price_type: int,
        price: float,
        strategy_name: str = "",
        order_remark: str = "",
    ) -> int: ...
    def cancel_order_stock(self, account: object, order_id: int) -> int: ...


@dataclass(frozen=True, slots=True)
class QmtOrderConstants:
    stock_buy: int
    stock_sell: int
    fix_price: int
    latest_price: int


@dataclass(slots=True)
class QmtOrderMapping:
    client_order_id: str
    idempotency_key: str
    qmt_seq: int
    strategy_id: str
    account_id: str
    order_remark: str
    qmt_order_id: int | None = None
    order_sysid: str | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class QmtSubmission:
    order: Order
    mapping: QmtOrderMapping


class SingleProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                handle.close()
                raise RuntimeError("another QMT trading gateway process is active") from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()
        self._handle = None


class QmtSimSafety:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.allowed_symbols = frozenset(settings.allowed_symbols)
        self.switches = TradingSwitches()
        self._accepted_keys: set[str] = set()
        self._minute_orders: deque[datetime] = deque()
        self._daily_notional = Decimal("0")
        self._daily_order_count = 0
        self._strategy_daily_notional: dict[str, Decimal] = {}
        self._trading_day: date | None = None

    def check(
        self,
        request: OrderRequest,
        *,
        account: Account | None,
        tick: Tick | None,
        connected: bool,
        subscribed: bool,
        account_healthy: bool,
        now: datetime,
    ) -> RiskResult:
        reason = self._reject_reason(
            request,
            account=account,
            tick=tick,
            connected=connected,
            subscribed=subscribed,
            account_healthy=account_healthy,
            now=now,
        )
        if reason is not None:
            return RiskResult(False, reason[0], reason[1], now)
        assert tick is not None
        price = request.limit_price or tick.last_price
        notional = price * request.quantity
        self._roll_day(now.astimezone(CHINA_TZ).date())
        self._accepted_keys.add(request.idempotency_key)
        self._minute_orders.append(now)
        self._daily_notional += notional
        self._daily_order_count += 1
        self._strategy_daily_notional[request.strategy_id] = (
            self._strategy_daily_notional.get(request.strategy_id, Decimal("0")) + notional
        )
        return RiskResult(True, "OK", f"{self.settings.execution_mode.value} order accepted", now)

    def _reject_reason(
        self,
        request: OrderRequest,
        *,
        account: Account | None,
        tick: Tick | None,
        connected: bool,
        subscribed: bool,
        account_healthy: bool,
        now: datetime,
    ) -> tuple[str, str] | None:
        settings = self.settings
        if settings.execution_mode not in {ExecutionMode.QMT_SIM, ExecutionMode.QMT_LIVE}:
            return "EXECUTION_DISABLED", "execution mode is not a QMT trading mode"
        if settings.execution_mode is ExecutionMode.QMT_SIM:
            if settings.enable_live_trading:
                return "LIVE_MODE_FORBIDDEN", "live trading cannot be enabled in qmt_sim"
            if not settings.enable_qmt_sim_trading:
                return "QMT_SIM_DISABLED", "QMT simulation trading is disabled"
        else:
            try:
                settings.validate_execution_safety()
            except RuntimeError:
                return "LIVE_RELEASE_GATE", "qmt_live release gate is not satisfied"
        if request.account_id != settings.qmt_account_id:
            return "ACCOUNT_NOT_ALLOWED", "account is not the configured QMT account"
        if request.symbol not in self.allowed_symbols:
            return "SYMBOL_NOT_ALLOWED", "symbol is not in the execution whitelist"
        if self.switches.global_stop or self.switches.cancel_only:
            return "KILL_SWITCH", "new orders are disabled"
        if request.account_id in (self.switches.stopped_accounts or set()):
            return "ACCOUNT_STOPPED", "account stop is active"
        if request.strategy_id in (self.switches.stopped_strategies or set()):
            return "STRATEGY_STOPPED", "strategy stop is active"
        if request.direction is Direction.BUY and self.switches.no_new_positions:
            return "NO_NEW_POSITIONS", "new positions are disabled"
        if not connected or not subscribed:
            return "GATEWAY_DISCONNECTED", "QMT gateway is not connected and subscribed"
        if not account_healthy or account is None:
            return "ACCOUNT_UNHEALTHY", "QMT account is unavailable"
        if tick is None:
            return "NO_MARKET", "real-time market snapshot is unavailable"
        if now - tick.timestamp > timedelta(seconds=10):
            return "STALE_MARKET", "real-time market snapshot is stale"
        if request.idempotency_key in self._accepted_keys:
            return "DUPLICATE_ORDER", "idempotency key has already been accepted"
        price = request.limit_price or tick.last_price
        notional = price * request.quantity
        if notional > settings.max_order_notional:
            return "MAX_ORDER_NOTIONAL", "order notional exceeds the configured limit"
        self._roll_day(now.astimezone(CHINA_TZ).date())
        if self._daily_notional + notional > settings.max_daily_notional:
            return "MAX_DAILY_NOTIONAL", "daily notional exceeds the configured limit"
        if self._daily_order_count >= settings.max_daily_orders:
            return "MAX_DAILY_ORDERS", "daily order count limit reached"
        strategy_notional = self._strategy_daily_notional.get(request.strategy_id, Decimal("0"))
        if strategy_notional + notional > settings.max_strategy_daily_notional:
            return "MAX_STRATEGY_DAILY_NOTIONAL", "strategy daily notional limit reached"
        cutoff = now - timedelta(minutes=1)
        while self._minute_orders and self._minute_orders[0] < cutoff:
            self._minute_orders.popleft()
        if len(self._minute_orders) >= settings.max_orders_per_minute:
            return "MAX_ORDERS_PER_MINUTE", "one-minute order limit reached"
        position = account.positions.get(request.symbol)
        current = position.market_value if position is not None else Decimal("0")
        if (
            request.direction is Direction.BUY
            and current + notional > settings.max_symbol_position_notional
        ):
            return "MAX_SYMBOL_POSITION", "projected symbol position exceeds the limit"
        if request.direction is Direction.BUY:
            total_position = sum(
                (position.market_value for position in account.positions.values()),
                Decimal("0"),
            )
            projected_total = total_position + notional
            if projected_total > settings.max_total_position_notional:
                return "MAX_TOTAL_POSITION", "projected total position exceeds the limit"
            if account.total_assets <= 0:
                return "INVALID_ACCOUNT_ASSETS", "account total assets must be positive"
            if projected_total / account.total_assets > settings.max_total_position_ratio:
                return "MAX_TOTAL_POSITION_RATIO", "projected total position ratio exceeds limit"
        if request.direction is Direction.BUY and account.available_cash < notional:
            return "INSUFFICIENT_CASH", "available cash is insufficient"
        if request.direction is Direction.SELL and (
            position is None or position.available_quantity < request.quantity
        ):
            return "INSUFFICIENT_POSITION", "sellable position is insufficient"
        return None

    def _roll_day(self, trading_day: date) -> None:
        if self._trading_day == trading_day:
            return
        self._trading_day = trading_day
        self._daily_notional = Decimal("0")
        self._daily_order_count = 0
        self._strategy_daily_notional.clear()
        self._minute_orders.clear()
        self._accepted_keys.clear()

    def restore_daily_usage(
        self,
        *,
        trading_day: date,
        notional: Decimal,
        idempotency_keys: set[str],
        order_timestamps: list[datetime],
        strategy_notional: dict[str, Decimal] | None = None,
    ) -> None:
        """Restore limits after restart so a restart cannot reset risk usage."""
        self._trading_day = trading_day
        self._daily_notional = max(notional, Decimal("0"))
        self._accepted_keys = set(idempotency_keys)
        self._daily_order_count = len(idempotency_keys)
        self._strategy_daily_notional = dict(strategy_notional or {})
        cutoff = datetime.now(UTC) - timedelta(minutes=1)
        self._minute_orders = deque(
            sorted(item for item in order_timestamps if item.astimezone(UTC) >= cutoff)
        )


class QmtSimTradingGateway:
    def __init__(
        self,
        *,
        settings: Settings,
        client: QmtClient,
        stock_account: object,
        order_constants: QmtOrderConstants,
        event_bus: EventBus | None = None,
        process_lock: SingleProcessLock | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.stock_account = stock_account
        self.order_constants = order_constants
        self.event_bus = event_bus or EventBus()
        self.process_lock = process_lock or SingleProcessLock(Path("logs/qmt_gateway.lock"))
        self.safety = QmtSimSafety(settings)
        self.connected = False
        self.subscribed = False
        self.account_healthy = False
        self.recovery_required = False
        self.recovery_reason: str | None = None
        self.last_error: str | None = None
        self.last_heartbeat: datetime | None = None
        self.last_account_query_at: datetime | None = None
        self.last_positions_query_at: datetime | None = None
        self.account: Account | None = None
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.last_queried_order_ids: set[str] = set()
        self.trades: dict[str, Trade] = {}
        self.mappings_by_seq: dict[int, QmtOrderMapping] = {}
        self.mappings_by_order_id: dict[int, QmtOrderMapping] = {}
        self.mappings_by_client_order_id: dict[str, QmtOrderMapping] = {}
        self.market: dict[str, Tick] = {}
        self._lock = threading.RLock()

    @classmethod
    def create(cls, settings: Settings | None = None) -> QmtSimTradingGateway:
        active = settings or get_settings()
        active.validate_execution_safety()
        if active.execution_mode not in {ExecutionMode.QMT_SIM, ExecutionMode.QMT_LIVE}:
            raise RuntimeError("QMT gateway requires execution_mode=qmt_sim or qmt_live")
        from xtquant import xtconstant
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        holder: dict[str, QmtSimTradingGateway] = {}
        callback = QmtSimCallback(lambda: holder["gateway"])
        session = SystemRandom().randint(100000, 999999999)
        client = cast(
            QmtClient,
            XtQuantTrader(str(active.qmt_userdata_path), session, callback),
        )
        gateway = cls(
            settings=active,
            client=client,
            stock_account=StockAccount(active.qmt_account_id, active.qmt_account_type),
            order_constants=QmtOrderConstants(
                stock_buy=int(xtconstant.STOCK_BUY),
                stock_sell=int(xtconstant.STOCK_SELL),
                fix_price=int(xtconstant.FIX_PRICE),
                latest_price=int(xtconstant.LATEST_PRICE),
            ),
        )
        holder["gateway"] = gateway
        return gateway

    def start(self) -> dict[str, object]:
        self.settings.validate_execution_safety()
        self.process_lock.acquire()
        try:
            self.event_bus.start_worker()
            self.client.start()
            self.connected = self.client.connect() == 0
            if not self.connected:
                raise RuntimeError("XtQuantTrader.connect failed")
            self.subscribed = self.client.subscribe(self.stock_account) == 0
            if not self.subscribed:
                raise RuntimeError("XtQuantTrader.subscribe failed")
            self.last_heartbeat = datetime.now(UTC)
            self.reconcile()
            return self.status()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        try:
            self.client.stop()
        finally:
            self.connected = False
            self.subscribed = False
            try:
                self.event_bus.stop_worker()
            finally:
                self.process_lock.release()

    def reconnect(self, max_attempts: int = 3) -> bool:
        for _ in range(max_attempts):
            try:
                self.connected = self.client.connect() == 0
                self.subscribed = self.connected and self.client.subscribe(self.stock_account) == 0
                if self.connected and self.subscribed:
                    self.last_error = "gateway reconnected; reconciliation required"
                    if self.recovery_reason == "gateway disconnected":
                        self.recovery_reason = "gateway reconnected after disconnect"
                    self.last_heartbeat = datetime.now(UTC)
                    self.reconcile()
                    return True
            except Exception as exc:
                self.last_error = type(exc).__name__
        return False

    def status(self) -> dict[str, object]:
        return {
            "execution_mode": self.settings.execution_mode.value,
            "connected": self.connected,
            "subscribed": self.subscribed,
            "account_healthy": self.account_healthy,
            "recovery_required": self.recovery_required,
            "recovery_reason": self.recovery_reason,
            "account": self._masked_account(),
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "account_data_source": "query_stock_asset/query_stock_positions",
            "last_account_query_at": (
                self.last_account_query_at.isoformat() if self.last_account_query_at else None
            ),
            "last_positions_query_at": (
                self.last_positions_query_at.isoformat() if self.last_positions_query_at else None
            ),
            "last_error": self.last_error,
            "kill_switch": self.safety.switches.global_stop,
            "cancel_only": self.safety.switches.cancel_only,
            "no_new_positions": self.safety.switches.no_new_positions,
            "execution_universe_symbol_count": len(self.safety.allowed_symbols),
            "execution_universe_release_id": self.settings.execution_universe_release_id or None,
            "event_bus": self.event_bus.snapshot(),
        }

    def update_market(self, tick: Tick) -> None:
        self.market[tick.symbol] = tick

    def submit_async(self, request: OrderRequest, now: datetime | None = None) -> QmtSubmission:
        current = now or datetime.now(UTC)
        if self.connected and self.subscribed:
            self._refresh_account_and_positions()
        risk = self.safety.check(
            request,
            account=self.account,
            tick=self.market.get(request.symbol),
            connected=self.connected,
            subscribed=self.subscribed,
            account_healthy=self.account_healthy,
            now=current,
        )
        self.event_bus.enqueue(Event(EventType.RISK, risk, source="qmt-sim-risk"))
        if not risk.accepted:
            raise PermissionError(f"{risk.code}: {risk.message}")
        price = request.limit_price or Decimal("0")
        qmt_seq = self.client.order_stock_async(
            self.stock_account,
            request.symbol,
            self.order_constants.stock_buy
            if request.direction is Direction.BUY
            else self.order_constants.stock_sell,
            request.quantity,
            self.order_constants.fix_price
            if request.limit_price is not None
            else self.order_constants.latest_price,
            float(price),
            request.strategy_id,
            request.client_order_id[:48],
        )
        if qmt_seq <= 0:
            raise RuntimeError("QMT async order did not return a valid sequence")
        order = Order(order_id=f"qmt-seq-{qmt_seq}", request=request)
        order.transition(OrderStatus.SUBMITTING, current)
        mapping = QmtOrderMapping(
            request.client_order_id,
            request.idempotency_key,
            qmt_seq,
            request.strategy_id,
            request.account_id,
            request.client_order_id[:48],
        )
        with self._lock:
            self.orders[order.order_id] = order
            self.mappings_by_seq[qmt_seq] = mapping
            self.mappings_by_client_order_id[mapping.client_order_id] = mapping
        self.event_bus.enqueue(Event(EventType.ORDER, order, source="qmt-sim-submit"))
        return QmtSubmission(order, mapping)

    def cancel(self, qmt_order_id: int) -> int:
        if not self.connected or not self.subscribed:
            raise RuntimeError("QMT simulation gateway is disconnected")
        with self._lock:
            order = self._find_order(qmt_order_id=qmt_order_id)
            if order is not None and order.status in {
                OrderStatus.RISK_REJECTED,
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
            }:
                raise RuntimeError(f"cannot cancel final order in state {order.status.value}")
        result = self.client.cancel_order_stock(self.stock_account, qmt_order_id)
        changed_order: Order | None = None
        if result == 0:
            with self._lock:
                order = self._find_order(qmt_order_id=qmt_order_id)
                if order is not None and order.status not in {
                    OrderStatus.RISK_REJECTED,
                    OrderStatus.FILLED,
                    OrderStatus.CANCELLED,
                    OrderStatus.REJECTED,
                    OrderStatus.CANCELLING,
                }:
                    order.transition(OrderStatus.CANCELLING, datetime.now(UTC))
                    changed_order = order
        if changed_order is not None:
            self.event_bus.enqueue(
                Event(EventType.ORDER, changed_order, source="qmt-sim-cancel-request")
            )
        return result

    def reconcile(self) -> dict[str, object]:
        account_snapshot = self._refresh_account_and_positions()
        orders = self.client.query_stock_orders(self.stock_account, False) or []
        trades = self.client.query_stock_trades(self.stock_account) or []
        converted_orders = list(map(order_from_xttrader, orders))
        converted_trades = [
            trade_from_xttrader(
                item,
                source="qmt_trade_query",
                execution_mode=self.settings.execution_mode.value,
                account_id=self.settings.qmt_account_id,
                is_simulated=(
                    self.settings.execution_mode is not ExecutionMode.QMT_LIVE
                    or self.settings.qmt_account_is_simulated
                ),
            )
            for item in trades
        ]
        with self._lock:
            for raw, order in zip(orders, converted_orders, strict=True):
                self._ingest_order(order, object_payload(raw))
            self.last_queried_order_ids = {order.order_id for order in converted_orders}
        with self._lock:
            for trade in converted_trades:
                self.trades[trade.trade_id] = trade
                matched_order = self._find_order(
                    qmt_order_id=trade.qmt_order_id,
                    order_sysid=trade.order_sysid,
                )
                if matched_order is not None:
                    self._apply_known_trades(matched_order)
        self.last_heartbeat = datetime.now(UTC)
        snapshot = {
            **account_snapshot,
            "orders": len(converted_orders),
            "trades": len(converted_trades),
            "reconciled_at": self.last_heartbeat.isoformat(),
        }
        self.event_bus.enqueue(Event(EventType.ACCOUNT, snapshot, source="qmt-sim-reconcile"))
        return snapshot

    def refresh_known_orders(self, qmt_order_ids: set[int]) -> int:
        """Query known broker order IDs that are absent from today's order list."""

        refreshed = 0
        queried_ids = {
            order.qmt_order_id
            for order_id in self.last_queried_order_ids
            if (order := self.orders.get(order_id)) is not None and order.qmt_order_id is not None
        }
        for qmt_order_id in sorted(qmt_order_ids - queried_ids):
            raw = self.client.query_stock_order(self.stock_account, qmt_order_id)
            if raw is None:
                continue
            converted = order_from_xttrader(raw)
            with self._lock:
                converted, _ = self._ingest_order(converted, object_payload(raw))
                self.last_queried_order_ids.add(converted.order_id)
            refreshed += 1
        return refreshed

    def _refresh_account_and_positions(self) -> dict[str, object]:
        """Read the broker's current funds and sellable positions as one risk snapshot."""

        queried_at = datetime.now(UTC)
        asset = self.client.query_stock_asset(self.stock_account)
        self.last_account_query_at = queried_at
        raw_positions = self.client.query_stock_positions(self.stock_account)
        self.last_positions_query_at = datetime.now(UTC)
        if asset is None or raw_positions is None:
            self.account_healthy = False
            self.last_error = (
                "QMT account query failed" if asset is None else "QMT positions query failed"
            )
            return {
                "account": False,
                "positions": len(self.positions),
                "account_query_at": queried_at.isoformat(),
            }

        account = account_from_xttrader(asset)
        positions = {item.symbol: item for item in map(position_from_xttrader, raw_positions)}
        account.positions = positions.copy()
        with self._lock:
            self.account = account
            self.positions = positions
            self.account_healthy = True
            self.last_error = None
        self.last_heartbeat = datetime.now(UTC)
        return {
            "account": True,
            "positions": len(positions),
            "account_query_at": queried_at.isoformat(),
        }

    def on_connected(self) -> None:
        self.connected = True
        self.last_heartbeat = datetime.now(UTC)

    def on_disconnected(self) -> None:
        if not self.connected and not self.subscribed:
            return
        self.connected = False
        self.subscribed = False
        self.recovery_required = True
        self.recovery_reason = "gateway disconnected"
        self.safety.switches.cancel_only = True
        self.last_error = "gateway disconnected"
        self.event_bus.enqueue(Event(EventType.GATEWAY, self.status(), source="qmt-sim-callback"))

    def on_account_status(self, value: object) -> None:
        payload = object_payload(value)
        self.account_healthy = int(payload.get("status", -1)) == 0
        if not self.account_healthy:
            self.recovery_required = True
            self.recovery_reason = "QMT account unhealthy"
            self.last_error = "QMT account unhealthy"
            self.safety.switches.cancel_only = True
        elif self.recovery_required and self.recovery_reason == "QMT account unhealthy":
            self.recovery_reason = "QMT account recovered"
        self.last_heartbeat = datetime.now(UTC)
        self.event_bus.enqueue(Event(EventType.ACCOUNT, payload, source="qmt-sim-callback"))

    def on_asset(self, value: object) -> None:
        self.account = account_from_xttrader(value)
        self.account.positions = self.positions.copy()
        self.account_healthy = True
        if self.recovery_required and self.recovery_reason == "QMT account unhealthy":
            self.recovery_reason = "QMT account recovered"
        self.event_bus.enqueue(Event(EventType.ACCOUNT, self.account, source="qmt-sim-callback"))

    def on_position(self, value: object) -> None:
        position = position_from_xttrader(value)
        self.positions[position.symbol] = position
        if self.account is not None:
            self.account.positions = self.positions.copy()
        self.event_bus.enqueue(Event(EventType.POSITION, position, source="qmt-sim-callback"))

    def on_order(self, value: object) -> None:
        order = order_from_xttrader(value)
        payload = object_payload(value)
        with self._lock:
            order, changed = self._ingest_order(order, payload)
        if not changed:
            return
        self.event_bus.enqueue(Event(EventType.ORDER, order, source="qmt-sim-callback"))

    def on_trade(self, value: object) -> None:
        trade = trade_from_xttrader(
            value,
            source="qmt_trade_callback",
            execution_mode=self.settings.execution_mode.value,
            account_id=self.settings.qmt_account_id,
            is_simulated=(
                self.settings.execution_mode is not ExecutionMode.QMT_LIVE
                or self.settings.qmt_account_is_simulated
            ),
        )
        with self._lock:
            if trade.trade_id in self.trades:
                return
            self.trades[trade.trade_id] = trade
            order = self._find_order(
                qmt_order_id=trade.qmt_order_id,
                order_sysid=trade.order_sysid,
            )
            order_changed = order is not None and self._apply_known_trades(order)
        self.event_bus.enqueue(Event(EventType.TRADE, trade, source="qmt-sim-callback"))
        if order_changed and order is not None:
            self.event_bus.enqueue(Event(EventType.ORDER, order, source="qmt-sim-trade"))

    def on_async_order_response(self, value: object) -> None:
        payload = object_payload(value)
        seq = int(payload.get("seq", 0))
        order_id = int(payload.get("order_id", 0))
        mapping = self.mappings_by_seq.get(seq)
        if mapping is None:
            return
        error = str(payload.get("error_msg") or "") or None
        if mapping.qmt_order_id == (order_id or None) and mapping.last_error == error:
            return
        mapping.qmt_order_id = order_id or None
        mapping.last_error = error
        if order_id:
            self.mappings_by_order_id[order_id] = mapping
        self.event_bus.enqueue(Event(EventType.ORDER, mapping, source="qmt-sim-async-response"))

    def on_order_error(self, value: object) -> None:
        payload = object_payload(value)
        seq = int(payload.get("seq", 0))
        mapping = self.mappings_by_seq.get(seq)
        qmt_order_id = int(payload.get("order_id", 0))
        if mapping is None and qmt_order_id:
            mapping = self.mappings_by_order_id.get(qmt_order_id)
        if mapping is None:
            mapping = self.mappings_by_client_order_id.get(str(payload.get("order_remark") or ""))
        message = str(payload.get("error_msg") or "QMT order error")
        if mapping is not None:
            mapping.last_error = message
        with self._lock:
            order = self._find_order(qmt_order_id=qmt_order_id or None)
            if order is None and mapping is not None:
                order = self.orders.get(f"qmt-seq-{mapping.qmt_seq}")
            if order is not None and order.status in {
                OrderStatus.CREATED,
                OrderStatus.SUBMITTING,
                OrderStatus.SUBMITTED,
                OrderStatus.CANCELLING,
                OrderStatus.UNKNOWN,
            }:
                order.transition(OrderStatus.REJECTED, datetime.now(UTC))
                order.reject_reason = message
        self.last_error = "QMT order rejected"
        if order is not None:
            self.event_bus.enqueue(Event(EventType.ORDER, order, source="qmt-sim-order-error"))
        self.event_bus.enqueue(Event(EventType.RISK, payload, source="qmt-sim-order-error"))

    def on_cancel_error(self, value: object) -> None:
        payload = object_payload(value)
        qmt_order_id = int(payload.get("order_id", 0))
        order_sysid = str(payload.get("order_sysid") or "") or None
        message = str(payload.get("error_msg") or "QMT cancel failed")
        with self._lock:
            mapping = self.mappings_by_order_id.get(qmt_order_id)
            if mapping is None and order_sysid:
                mapping = next(
                    (
                        item
                        for item in self.mappings_by_seq.values()
                        if item.order_sysid == order_sysid
                    ),
                    None,
                )
            if mapping is not None:
                mapping.last_error = message
            order = self._find_order(
                qmt_order_id=qmt_order_id or None,
                order_sysid=order_sysid,
            )
            if order is not None and order.status is OrderStatus.CANCELLING:
                target = (
                    OrderStatus.PARTIALLY_FILLED
                    if order.filled_quantity > 0
                    else OrderStatus.SUBMITTED
                )
                order.transition(target, datetime.now(UTC))
        self.last_error = "QMT cancel failed"
        if order is not None:
            self.event_bus.enqueue(Event(EventType.ORDER, order, source="qmt-sim-cancel-error"))
        self.event_bus.enqueue(Event(EventType.RISK, payload, source="qmt-sim-cancel-error"))

    def _ingest_order(self, order: Order, payload: dict[str, Any]) -> tuple[Order, bool]:
        reported_order_id = order.order_id
        reported_broker_order_id = order.broker_order_id
        reported_qmt_order_id = order.qmt_order_id
        reported_order_sysid = order.order_sysid
        qmt_order_id = int(payload.get("order_id", 0))
        mapping = self.mappings_by_order_id.get(qmt_order_id)
        if mapping is None:
            mapping = self.mappings_by_client_order_id.get(str(payload.get("order_remark") or ""))
        pending: Order | None = None
        if mapping is not None:
            mapping.qmt_order_id = qmt_order_id or mapping.qmt_order_id
            mapping.order_sysid = str(payload.get("order_sysid") or "") or None
            if qmt_order_id:
                self.mappings_by_order_id[qmt_order_id] = mapping
            pending = self.orders.pop(f"qmt-seq-{mapping.qmt_seq}", None)
        if pending is not None:
            order.request = replace(pending.request, account_id="masked")
            order = _merge_callback_order(pending, order)
        existing = self.orders.get(order.order_id)
        if existing is not None:
            before = _order_fact(existing)
            order.request = replace(existing.request, account_id="masked")
            order = _merge_callback_order(existing, order)
            changed = _order_fact(order) != before
        else:
            changed = True
        order.order_id = reported_order_id
        order.broker_order_id = reported_broker_order_id
        order.qmt_order_id = reported_qmt_order_id
        order.order_sysid = reported_order_sysid
        self.orders[order.order_id] = order
        return order, changed

    def _find_order(
        self,
        *,
        qmt_order_id: int | None = None,
        order_sysid: str | None = None,
    ) -> Order | None:
        for identity in (
            str(qmt_order_id) if qmt_order_id else None,
            order_sysid,
        ):
            if identity and identity in self.orders:
                return self.orders[identity]
        mapping = self.mappings_by_order_id.get(qmt_order_id or 0)
        if mapping is None and order_sysid:
            mapping = next(
                (item for item in self.mappings_by_seq.values() if item.order_sysid == order_sysid),
                None,
            )
        if mapping is not None:
            return self.orders.get(f"qmt-seq-{mapping.qmt_seq}")
        return None

    def _apply_known_trades(self, order: Order) -> bool:
        related = [
            trade
            for trade in self.trades.values()
            if (order.qmt_order_id is not None and trade.qmt_order_id == order.qmt_order_id)
            or (order.order_sysid and trade.order_sysid == order.order_sysid)
            or (order.broker_order_id and trade.broker_order_id == order.broker_order_id)
        ]
        if not related:
            mapping = next(
                (
                    item
                    for item in self.mappings_by_seq.values()
                    if self.orders.get(f"qmt-seq-{item.qmt_seq}") is order
                ),
                None,
            )
            if mapping is not None:
                related = [
                    trade
                    for trade in self.trades.values()
                    if (
                        mapping.qmt_order_id is not None
                        and trade.qmt_order_id == mapping.qmt_order_id
                    )
                    or (mapping.order_sysid and trade.order_sysid == mapping.order_sysid)
                ]
                if related:
                    order.qmt_order_id = mapping.qmt_order_id
                    order.order_sysid = mapping.order_sysid
                    order.broker_order_id = related[0].broker_order_id
        known_quantity = sum(item.quantity for item in related)
        if known_quantity <= order.filled_quantity:
            return False
        order.filled_quantity = known_quantity
        order.average_fill_price = (
            sum((item.price * item.quantity for item in related), Decimal("0")) / known_quantity
        )
        order.updated_at = max(item.timestamp for item in related)
        if known_quantity >= order.request.quantity:
            order.status = OrderStatus.FILLED
        elif order.status not in {OrderStatus.CANCELLING, OrderStatus.CANCELLED}:
            order.status = OrderStatus.PARTIALLY_FILLED
        return True

    def _masked_account(self) -> str | None:
        value = self.settings.qmt_account_id
        return f"***{value[-4:]}" if value else None


def _merge_callback_order(existing: Order, incoming: Order) -> Order:
    """Resolve duplicate/out-of-order terminal callbacks conservatively."""
    if existing.status is OrderStatus.FILLED and incoming.status is not OrderStatus.FILLED:
        return existing
    if incoming.filled_quantity < existing.filled_quantity:
        incoming.filled_quantity = existing.filled_quantity
        incoming.average_fill_price = existing.average_fill_price
    if existing.status is OrderStatus.CANCELLED and incoming.status in {
        OrderStatus.SUBMITTING,
        OrderStatus.SUBMITTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCELLING,
        OrderStatus.UNKNOWN,
    }:
        if incoming.filled_quantity > existing.filled_quantity:
            existing.filled_quantity = incoming.filled_quantity
            existing.average_fill_price = incoming.average_fill_price
            existing.updated_at = incoming.updated_at or existing.updated_at
        return existing
    if existing.status is OrderStatus.REJECTED and incoming.status in {
        OrderStatus.SUBMITTING,
        OrderStatus.SUBMITTED,
        OrderStatus.CANCELLING,
        OrderStatus.UNKNOWN,
    }:
        return existing
    if existing.status is OrderStatus.CANCELLING and incoming.status in {
        OrderStatus.SUBMITTING,
        OrderStatus.SUBMITTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.UNKNOWN,
    }:
        if incoming.filled_quantity > existing.filled_quantity:
            existing.filled_quantity = incoming.filled_quantity
            existing.average_fill_price = incoming.average_fill_price
            existing.updated_at = incoming.updated_at or existing.updated_at
        return existing
    if existing.status is OrderStatus.PARTIALLY_FILLED and incoming.status in {
        OrderStatus.SUBMITTING,
        OrderStatus.SUBMITTED,
        OrderStatus.UNKNOWN,
    }:
        return existing
    if existing.status is OrderStatus.SUBMITTED and incoming.status in {
        OrderStatus.SUBMITTING,
        OrderStatus.UNKNOWN,
    }:
        return existing
    return incoming


def _order_fact(order: Order) -> tuple[object, ...]:
    return (
        order.status,
        order.filled_quantity,
        order.average_fill_price,
        order.qmt_order_id,
        order.order_sysid,
    )


class QmtSimCallback:
    def __init__(self, gateway: Callable[[], QmtSimTradingGateway]) -> None:
        self._gateway = gateway

    def on_connected(self) -> None:
        self._gateway().on_connected()

    def on_disconnected(self) -> None:
        self._gateway().on_disconnected()

    def on_account_status(self, status: object) -> None:
        self._gateway().on_account_status(status)

    def on_stock_asset(self, asset: object) -> None:
        self._gateway().on_asset(asset)

    def on_stock_position(self, position: object) -> None:
        self._gateway().on_position(position)

    def on_stock_order(self, order: object) -> None:
        self._gateway().on_order(order)

    def on_stock_trade(self, trade: object) -> None:
        self._gateway().on_trade(trade)

    def on_order_stock_async_response(self, response: object) -> None:
        self._gateway().on_async_order_response(response)

    def on_order_error(self, error: object) -> None:
        self._gateway().on_order_error(error)

    def on_cancel_error(self, error: object) -> None:
        self._gateway().on_cancel_error(error)
