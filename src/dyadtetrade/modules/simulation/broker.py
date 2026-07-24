from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pandas as pd

from dyadtetrade.domain import (
    Account,
    Bar,
    CancelRequest,
    Direction,
    Instrument,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    Trade,
)
from dyadtetrade.modules.simulation.commission import AShareCommission
from dyadtetrade.modules.simulation.constraints import default_instrument, validate_order_request
from dyadtetrade.modules.simulation.matcher import BarMatcher


class SimulatedBroker:
    def __init__(
        self,
        *,
        initial_cash: Decimal,
        commission: AShareCommission,
        matcher: BarMatcher,
        instruments: dict[str, Instrument] | None = None,
        enforce_t1: bool = True,
        execution_delay_bars: int = 1,
        max_history_bars: int | None = None,
    ) -> None:
        self.account = Account("backtest", initial_cash, initial_cash)
        self.commission = commission
        self.matcher = matcher
        self.instruments = instruments or {}
        self.enforce_t1 = enforce_t1
        if execution_delay_bars < 1:
            raise ValueError("execution_delay_bars must be at least one")
        self.execution_delay_bars = execution_delay_bars
        if max_history_bars is not None and max_history_bars < 1:
            raise ValueError("max_history_bars must be positive")
        self.max_history_bars = max_history_bars
        self.orders: list[Order] = []
        self.trades: list[Trade] = []
        self._orders_by_id: dict[str, Order] = {}
        self._orders_by_key: dict[str, Order] = {}
        self._current: dict[str, Bar] = {}
        self._history: list[Bar] = []
        self._now = datetime.min
        self._records: list[dict[str, float]] = []
        self._scheduled: list[tuple[datetime, str, Callable[[], None]]] = []
        self._bar_sequence = 0
        self._eligible_sequence: dict[str, int] = {}

    @property
    def now(self) -> datetime:
        return self._now

    def set_time(self, value: datetime) -> None:
        self._now = value

    def history(self, symbols: list[str], count: int, fields: list[str]) -> pd.DataFrame:
        selected = [bar for bar in self._history if bar.symbol in symbols]
        rows = [
            {
                "datetime": bar.timestamp,
                "symbol": bar.symbol,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": bar.volume,
            }
            for bar in selected
        ]
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        columns = [column for column in fields if column in frame.columns]
        return frame.groupby("symbol", group_keys=False).tail(count).set_index("datetime")[columns]

    def current(self, symbol: str) -> Bar | None:
        return self._current.get(symbol)

    def get_account(self) -> Account:
        return self.account

    def submit_order(self, request: OrderRequest) -> Order:
        existing = self._orders_by_key.get(request.idempotency_key)
        if existing is not None:
            return existing
        order = Order(uuid4().hex, request)
        self.orders.append(order)
        self._orders_by_id[order.order_id] = order
        self._orders_by_key[request.idempotency_key] = order
        bar = self._current.get(request.symbol)
        if bar is None:
            order.reject_reason = "no current market data"
            order.transition(OrderStatus.RISK_REJECTED, self.now)
            return order
        instrument = self.instruments.get(request.symbol, default_instrument(request.symbol))
        position = self.account.positions.get(request.symbol)
        available_quantity = position.available_quantity if position else 0
        estimated = self.commission.calculate(request.direction, bar.close, request.quantity)
        check = validate_order_request(
            request,
            instrument,
            available_cash=self.account.available_cash,
            available_quantity=available_quantity,
            reference_price=bar.close,
            estimated_fee=estimated.total,
            trade_date=self.now.date(),
        )
        if not check.accepted:
            order.reject_reason = f"{check.code}: {check.message}"
            order.transition(OrderStatus.RISK_REJECTED, self.now)
            return order
        order.transition(OrderStatus.SUBMITTING, self.now)
        if request.direction is Direction.BUY:
            reserve = bar.close * request.quantity + estimated.total
            order.frozen_cash = reserve
            self.account.frozen_cash += reserve
            self.account.available_cash -= reserve
        else:
            assert position is not None
            position.available_quantity -= request.quantity
            position.frozen_quantity += request.quantity
            order.frozen_quantity = request.quantity
        order.submitted_at = self.now
        self._eligible_sequence[order.order_id] = self._bar_sequence + self.execution_delay_bars
        order.transition(OrderStatus.SUBMITTED, self.now)
        self.account.assert_identity()
        return order

    def cancel_order(self, request: CancelRequest) -> Order | None:
        order = self._orders_by_id.get(request.order_id)
        if order is None:
            return None
        if order.status not in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
            return order
        order.transition(OrderStatus.CANCELLING, self.now)
        self._release_order_reserve(order)
        order.transition(OrderStatus.CANCELLED, self.now)
        self.account.assert_identity()
        return order

    def restore_order(self, order: Order) -> None:
        if order.order_id in self._orders_by_id:
            return
        self.orders.append(order)
        self._orders_by_id[order.order_id] = order
        self._orders_by_key[order.request.idempotency_key] = order
        self._eligible_sequence[order.order_id] = self._bar_sequence + self.execution_delay_bars

    def match(self, bars: list[Bar]) -> list[Trade]:
        self._bar_sequence += 1
        by_symbol = {bar.symbol: bar for bar in bars}
        new_trades: list[Trade] = []
        for order in self.orders:
            if order.status not in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
                continue
            if self._bar_sequence < self._eligible_sequence.get(order.order_id, 0):
                continue
            bar = by_symbol.get(order.request.symbol)
            submitted_same_or_later = (
                bar is not None
                and order.submitted_at is not None
                and bar.timestamp <= order.submitted_at
            )
            if bar is None or submitted_same_or_later:
                continue
            instrument = self.instruments.get(
                order.request.symbol, default_instrument(order.request.symbol)
            )
            match = self.matcher.match(order, bar, instrument)
            if match.quantity <= 0 or match.price is None:
                continue
            trade = self._apply_fill(order, match.quantity, match.price)
            new_trades.append(trade)
        return new_trades

    def mark(self, bars: list[Bar]) -> None:
        self._current = {bar.symbol: bar for bar in bars}
        self._history.extend(bars)
        if self.max_history_bars is not None and len(self._history) > self.max_history_bars:
            del self._history[: -self.max_history_bars]
        for bar in bars:
            position = self.account.positions.get(bar.symbol)
            if position is not None:
                position.last_price = bar.close
        self.account.assert_identity()

    def settle_new_day(self) -> None:
        for position in self.account.positions.values():
            if self.enforce_t1:
                position.available_quantity += position.today_bought
                position.today_bought = 0

    def schedule(self, name: str, delay: timedelta, callback: Callable[[], None]) -> None:
        self._scheduled.append((self.now + delay, name, callback))

    def run_scheduled(self) -> None:
        due = [item for item in self._scheduled if item[0] <= self.now]
        self._scheduled = [item for item in self._scheduled if item[0] > self.now]
        for _, _, callback in due:
            callback()

    def record(self, values: dict[str, float]) -> None:
        self._records.append({"timestamp": self.now.timestamp(), **values})

    @property
    def records(self) -> list[dict[str, float]]:
        return list(self._records)

    def _apply_fill(self, order: Order, quantity: int, price: Decimal) -> Trade:
        fees = self.commission.calculate(order.request.direction, price, quantity)
        if order.request.direction is Direction.BUY:
            self.account.cash -= price * quantity + fees.total
            position = self.account.positions.setdefault(
                order.request.symbol, Position(order.request.symbol)
            )
            old_cost = position.average_cost * position.quantity
            position.quantity += quantity
            position.today_bought += quantity
            if not self.enforce_t1:
                position.available_quantity += quantity
            position.average_cost = (old_cost + price * quantity + fees.total) / position.quantity
            position.last_price = price
        else:
            position = self.account.positions[order.request.symbol]
            position.quantity -= quantity
            position.frozen_quantity -= quantity
            order.frozen_quantity -= quantity
            self.account.cash += price * quantity - fees.total
            position.last_price = price
            if position.quantity == 0:
                position.average_cost = Decimal("0")
        order.apply_fill(quantity, price, self.now)
        self._resize_order_reserve(order)
        self.account.available_cash = self.account.cash - self.account.frozen_cash
        trade = Trade(
            trade_id=uuid4().hex,
            order_id=order.order_id,
            symbol=order.request.symbol,
            direction=order.request.direction,
            quantity=quantity,
            price=price,
            timestamp=self.now,
            commission=fees.commission,
            tax=fees.stamp_duty,
            transfer_fee=fees.transfer_fee,
        )
        self.trades.append(trade)
        self.account.assert_identity()
        return trade

    def _resize_order_reserve(self, order: Order) -> None:
        if order.request.direction is Direction.BUY:
            self.account.frozen_cash -= order.frozen_cash
            order.frozen_cash = Decimal("0")
            if order.remaining_quantity > 0:
                bar = self._current[order.request.symbol]
                estimated = self.commission.calculate(
                    order.request.direction, bar.close, order.remaining_quantity
                )
                reserve = bar.close * order.remaining_quantity + estimated.total
                reserve = min(reserve, self.account.cash)
                order.frozen_cash = reserve
                self.account.frozen_cash += reserve
        elif order.status is OrderStatus.FILLED:
            order.frozen_quantity = 0

    def _release_order_reserve(self, order: Order) -> None:
        if order.request.direction is Direction.BUY:
            self.account.frozen_cash -= order.frozen_cash
            order.frozen_cash = Decimal("0")
            self.account.available_cash = self.account.cash - self.account.frozen_cash
        else:
            position = self.account.positions[order.request.symbol]
            position.frozen_quantity -= order.frozen_quantity
            position.available_quantity += order.frozen_quantity
            order.frozen_quantity = 0
