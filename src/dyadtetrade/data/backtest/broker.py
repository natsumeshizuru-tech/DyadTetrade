from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from dyadtetrade.data.backtest.models import (
    EquityPoint,
    HistoricalBacktestSettings,
    HistoricalOrder,
    HistoricalPosition,
    HistoricalTrade,
    Side,
)

MONEY = Decimal("0.0001")


class HistoricalBroker:
    def __init__(self, settings: HistoricalBacktestSettings) -> None:
        self.settings = settings
        self.cash = settings.initial_cash
        self.positions: dict[str, HistoricalPosition] = {}
        self.orders: list[HistoricalOrder] = []
        self.trades: list[HistoricalTrade] = []
        self.equity: list[EquityPoint] = []
        self._pending: list[HistoricalOrder] = []
        self._trade_date: int | None = None

    def begin_trade_date(self, trade_date: int) -> None:
        if self._trade_date == trade_date:
            return
        if self._trade_date is not None:
            for position in self.positions.values():
                position.available += position.today_buys
                position.today_buys = 0
        self._trade_date = trade_date

    def submit(self, symbol: str, side: Side, quantity: int, submitted_ts: int) -> int:
        order = HistoricalOrder(
            order_id=len(self.orders) + 1,
            symbol=symbol,
            side=side,
            quantity=quantity,
            submitted_ts=submitted_ts,
        )
        if quantity <= 0:
            return self._reject(order, "quantity_must_be_positive")
        if side == "buy" and quantity % self.settings.buy_lot_size != 0:
            return self._reject(order, "buy_quantity_must_match_lot_size")
        if side == "sell":
            position = self.positions.get(symbol)
            if position is None or position.available < quantity:
                return self._reject(order, "insufficient_available_position")
        self.orders.append(order)
        self._pending.append(order)
        return order.order_id

    def execute_pending(self, bars: dict[str, dict[str, object]], ts: int) -> None:
        pending = list(self._pending)
        self._pending.clear()
        for order in pending:
            bar = bars.get(order.symbol)
            if bar is None:
                self._reject_existing(order, "missing_bar")
                continue
            if int(str(bar.get("suspend_flag") or 0)) == 1:
                self._reject_existing(order, "suspended")
                continue
            if self._locked_at_limit(order.side, bar):
                self._reject_existing(order, "locked_at_price_limit")
                continue
            raw_open = bar.get("open")
            if raw_open is None:
                self._reject_existing(order, "missing_open_price")
                continue
            price = self._slipped(Decimal(str(raw_open)), order.side)
            notional = price * order.quantity
            commission = max(
                self.settings.minimum_commission,
                notional * self.settings.commission_rate,
            ).quantize(MONEY, rounding=ROUND_HALF_UP)
            stamp = (
                notional * self.settings.stamp_duty_rate if order.side == "sell" else Decimal("0")
            ).quantize(MONEY, rounding=ROUND_HALF_UP)
            transfer = (notional * self.settings.transfer_fee_rate).quantize(
                MONEY, rounding=ROUND_HALF_UP
            )
            if order.side == "buy":
                total = notional + commission + transfer
                if self.cash < total:
                    self._reject_existing(order, "insufficient_cash")
                    continue
                self.cash -= total
                buy_position = self.positions.setdefault(
                    order.symbol, HistoricalPosition(symbol=order.symbol)
                )
                old_notional = buy_position.average_cost * buy_position.quantity
                buy_position.quantity += order.quantity
                buy_position.today_buys += order.quantity
                if not self.settings.enforce_t1:
                    buy_position.available += order.quantity
                buy_position.average_cost = (old_notional + total) / buy_position.quantity
            else:
                sell_position = self.positions.get(order.symbol)
                if sell_position is None or sell_position.available < order.quantity:
                    self._reject_existing(order, "insufficient_available_position")
                    continue
                sell_position.quantity -= order.quantity
                sell_position.available -= order.quantity
                self.cash += notional - commission - stamp - transfer
            order.status = "filled"
            order.filled_ts = ts
            order.filled_price = price
            self.trades.append(
                HistoricalTrade(
                    trade_id=len(self.trades) + 1,
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    price=price,
                    commission=commission,
                    stamp_duty=stamp,
                    transfer_fee=transfer,
                    ts=ts,
                )
            )

    def mark_to_market(self, ts: int, closes: dict[str, object]) -> EquityPoint:
        position_value = Decimal("0")
        for symbol, position in self.positions.items():
            close = closes.get(symbol)
            if close is not None:
                position_value += Decimal(str(close)) * position.quantity
        point = EquityPoint(
            ts=ts,
            cash=self.cash.quantize(MONEY, rounding=ROUND_HALF_UP),
            position_value=position_value.quantize(MONEY, rounding=ROUND_HALF_UP),
            total_assets=(self.cash + position_value).quantize(MONEY, rounding=ROUND_HALF_UP),
        )
        self.equity.append(point)
        return point

    def _reject(self, order: HistoricalOrder, reason: str) -> int:
        order.status = "rejected"
        order.reason = reason
        self.orders.append(order)
        return order.order_id

    @staticmethod
    def _reject_existing(order: HistoricalOrder, reason: str) -> None:
        order.status = "rejected"
        order.reason = reason

    def _slipped(self, price: Decimal, side: Side) -> Decimal:
        direction = Decimal("1") if side == "buy" else Decimal("-1")
        multiplier = Decimal("1") + direction * self.settings.slippage_bps / Decimal("10000")
        return (price * multiplier).quantize(MONEY, rounding=ROUND_HALF_UP)

    def _locked_at_limit(self, side: Side, bar: dict[str, object]) -> bool:
        pre_close = bar.get("pre_close")
        high = bar.get("high")
        low = bar.get("low")
        if pre_close is None or high is None or low is None:
            return False
        reference = Decimal(str(pre_close))
        upper = reference * (Decimal("1") + self.settings.limit_ratio)
        lower = reference * (Decimal("1") - self.settings.limit_ratio)
        return (side == "buy" and Decimal(str(low)) >= upper) or (
            side == "sell" and Decimal(str(high)) <= lower
        )
