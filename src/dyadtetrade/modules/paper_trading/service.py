from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from dyadtetrade.domain import Bar, CancelRequest, Order, OrderRequest, OrderStatus
from dyadtetrade.modules.oms import OrderManagementService
from dyadtetrade.modules.risk import RiskEngine
from dyadtetrade.modules.simulation import AShareCommission, BarMatcher, SimulatedBroker


class PaperTradingService:
    """Bar-driven paper broker. It has no XtTrader dependency or live order path."""

    def __init__(
        self,
        *,
        initial_cash: Decimal = Decimal("1000000"),
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self.broker = SimulatedBroker(
            initial_cash=initial_cash,
            commission=AShareCommission(),
            matcher=BarMatcher(max_volume_participation=Decimal("0.1")),
            enforce_t1=True,
            max_history_bars=20_000,
        )
        # Paper runtimes can be started before the first market event.  Give
        # operational lifecycle timestamps a real clock instead of datetime.min.
        self.broker.set_time(datetime.now(UTC))
        self.broker.account.account_id = "paper"
        self.risk = risk_engine or RiskEngine()
        self.oms = OrderManagementService()
        self.last_trade_date: date | None = None
        self.last_reconciled_at: datetime | None = None
        self.strategy_heartbeats: dict[str, datetime] = {}

    def on_bars(self, timestamp: datetime, bars: list[Bar]) -> None:
        previous_total_assets = self.broker.account.total_assets
        self.broker.set_time(timestamp)
        if self.last_trade_date != timestamp.date():
            self.broker.settle_new_day()
            self.risk.reset_day()
            self.last_trade_date = timestamp.date()
        for trade in self.broker.match(bars):
            order = self.oms.orders.get(trade.order_id)
            paper_trade = replace(
                trade,
                execution_mode="paper",
                account_id="paper",
                strategy_id=order.request.strategy_id if order is not None else "",
                source="paper_match",
                is_simulated=True,
            )
            self.oms.record_trade(paper_trade)
        self.broker.mark(bars)
        self.risk.monitor_intraday(
            now=timestamp,
            account=self.broker.account,
            last_market_at=max((bar.timestamp for bar in bars), default=None),
            gateway_connected=True,
            strategy_heartbeats=self.strategy_heartbeats,
            active_orders=self.oms.active_orders(),
            previous_total_assets=previous_total_assets,
        )
        self.reconcile(timestamp)

    def heartbeat_strategy(self, strategy_id: str, at: datetime) -> None:
        self.strategy_heartbeats[strategy_id] = at

    def submit(self, request: OrderRequest) -> Order:
        market = self.broker.current(request.symbol)
        result = self.risk.check_order(
            request,
            account=self.broker.account,
            market=market,
            now=self.broker.now,
        )
        if not result.accepted:
            order = Order(f"RISK-{request.client_order_id}", request)
            order.reject_reason = f"{result.code}: {result.message}"
            order.transition(OrderStatus.RISK_REJECTED, self.broker.now)
            return self.oms.register(order, self.broker.now)
        return self.oms.register(self.broker.submit_order(request), self.broker.now)

    def cancel(self, order_id: str) -> Order | None:
        request = CancelRequest(order_id, requested_at=self.broker.now)
        self.oms.request_cancel(request, self.broker.now)
        self.risk.record_cancel()
        return self.broker.cancel_order(request)

    def reconcile(self, at: datetime) -> None:
        self.broker.account.assert_identity()
        for order in self.broker.orders:
            if order.order_id not in self.oms.orders:
                self.oms.register(order, at)
        self.last_reconciled_at = at

    def snapshot(self) -> dict[str, object]:
        account = self.broker.account
        return {
            "mode": "paper",
            "account": {
                "account_id": account.account_id,
                "cash": str(account.cash),
                "available_cash": str(account.available_cash),
                "frozen_cash": str(account.frozen_cash),
                "positions_value": str(account.positions_value),
                "total_assets": str(account.total_assets),
            },
            "positions": [
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "available_quantity": position.available_quantity,
                    "frozen_quantity": position.frozen_quantity,
                    "average_cost": str(position.average_cost),
                    "last_price": str(position.last_price),
                }
                for position in account.positions.values()
            ],
            "orders": [
                {
                    "order_id": order.order_id,
                    "symbol": order.request.symbol,
                    "direction": order.request.direction.value,
                    "quantity": order.request.quantity,
                    "filled_quantity": order.filled_quantity,
                    "status": order.status.value,
                    "reject_reason": order.reject_reason,
                }
                for order in self.oms.orders.values()
            ],
            "trades": [
                {
                    "trade_id": trade.trade_id,
                    "order_id": trade.order_id,
                    "symbol": trade.symbol,
                    "quantity": trade.quantity,
                    "price": str(trade.price),
                    "fees": str(trade.total_fees),
                    "source": trade.source,
                    "execution_mode": trade.execution_mode,
                    "is_simulated": trade.is_simulated,
                    "timestamp": trade.timestamp.isoformat(),
                }
                for trade in self.oms.trades.values()
            ],
            "risk": {
                "global_stop": self.risk.switches.global_stop,
                "cancel_only": self.risk.switches.cancel_only,
                "no_new_positions": self.risk.switches.no_new_positions,
                "daily_order_count": self.risk.daily_order_count,
                "daily_turnover": str(self.risk.daily_turnover),
                "events": [
                    {
                        "code": event.code,
                        "message": event.message,
                        "checked_at": event.checked_at.isoformat(),
                        "details": event.details,
                    }
                    for event in self.risk.events[-100:]
                ],
            },
            "last_reconciled_at": (
                self.last_reconciled_at.isoformat() if self.last_reconciled_at else None
            ),
        }
