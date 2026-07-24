from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import pandas as pd

from dyadtetrade.domain import (
    Account,
    Bar,
    CancelRequest,
    Order,
    OrderRequest,
    StrategyState,
    Tick,
)
from dyadtetrade.modules.paper_trading.service import PaperTradingService
from dyadtetrade.modules.strategy import StrategyContext, strategy_registry


class PaperStrategyRuntime:
    def __init__(self, service: PaperTradingService) -> None:
        self.service = service

    @property
    def now(self) -> datetime:
        return self.service.broker.now

    def history(self, symbols: list[str], count: int, fields: list[str]) -> pd.DataFrame:
        return self.service.broker.history(symbols, count, fields)

    def current(self, symbol: str) -> Bar | None:
        return self.service.broker.current(symbol)

    def get_account(self) -> Account:
        return self.service.broker.account

    def submit_order(self, request: OrderRequest) -> Order:
        return self.service.submit(request)

    def cancel_order(self, request: CancelRequest) -> Order | None:
        return self.service.cancel(request.order_id)

    def schedule(self, name: str, delay: timedelta, callback: Callable[[], None]) -> None:
        self.service.broker.schedule(name, delay, callback)

    def record(self, values: dict[str, float]) -> None:
        self.service.broker.record(values)


class PaperStrategyRunner:
    def __init__(
        self,
        service: PaperTradingService,
        strategy_name: str,
        strategy_id: str,
        parameters: dict[str, object] | None = None,
    ) -> None:
        self.service = service
        self.parameters = dict(parameters or {})
        self.strategy = strategy_registry.create(strategy_name, strategy_id, parameters or {})
        self.runtime = PaperStrategyRuntime(service)
        self.context = StrategyContext(strategy_id, self.runtime)
        self.running = False
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.last_heartbeat: datetime | None = None
        self._order_statuses: dict[str, str] = {}
        self._trade_ids: set[str] = set()

    def start(self) -> None:
        if self.running:
            return
        self.strategy.on_init(self.context)
        self.strategy.on_start(self.context)
        self.running = True
        self.started_at = self.runtime.now
        self.stopped_at = None

    def stop(self) -> None:
        if not self.running:
            return
        self.strategy.on_stop(self.context)
        self.running = False
        self.stopped_at = self.runtime.now

    def on_bars(self, timestamp: datetime, bars: list[Bar]) -> None:
        self.service.on_bars(timestamp, bars)
        self.after_market_bars(bars)

    def after_market_bars(self, bars: list[Bar]) -> None:
        if not self.running:
            return
        self._dispatch_order_and_trade_updates()
        self.last_heartbeat = self.runtime.now
        self.service.heartbeat_strategy(self.strategy.strategy_id, self.runtime.now)
        for bar in bars:
            self.strategy.on_bar(self.context, bar)
        self.service.broker.run_scheduled()
        self._dispatch_order_and_trade_updates()

    def after_market_ticks(self, ticks: list[Tick], simulated_daily_bars: list[Bar]) -> None:
        """Dispatch one real-time batch after the shared paper broker is marked once."""
        if not self.running:
            return
        self._dispatch_order_and_trade_updates()
        self.last_heartbeat = self.runtime.now
        self.service.heartbeat_strategy(self.strategy.strategy_id, self.runtime.now)
        for tick in ticks:
            self.strategy.on_tick(self.context, tick)
        for bar in simulated_daily_bars:
            self.strategy.on_bar(self.context, bar)
        self.service.broker.run_scheduled()
        self._dispatch_order_and_trade_updates()

    def snapshot(self) -> StrategyState:
        return self.strategy.snapshot(self.runtime.now)

    def restore(self, state: StrategyState) -> None:
        self.strategy.restore(state)

    def status(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy.strategy_id,
            "name": self.strategy.name,
            "version": self.strategy.version,
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "state": self.snapshot().payload,
            "parameters": self.parameters,
            "order_count": sum(
                order.request.strategy_id == self.strategy.strategy_id
                for order in self.service.oms.orders.values()
            ),
            "trade_count": sum(
                self.service.oms.orders.get(trade.order_id) is not None
                and self.service.oms.orders[trade.order_id].request.strategy_id
                == self.strategy.strategy_id
                for trade in self.service.oms.trades.values()
            ),
            "record_count": len(self.service.broker.records),
            "mode": "paper",
            "live_order_capability": False,
        }

    def _dispatch_order_and_trade_updates(self) -> None:
        for order in self.service.oms.orders.values():
            status = order.status.value
            if self._order_statuses.get(order.order_id) != status:
                self.strategy.on_order(self.context, order)
                self._order_statuses[order.order_id] = status
        for trade in self.service.oms.trades.values():
            if trade.trade_id not in self._trade_ids:
                self.strategy.on_trade(self.context, trade)
                self._trade_ids.add(trade.trade_id)
