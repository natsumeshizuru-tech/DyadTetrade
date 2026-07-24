from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import pandas as pd
from pydantic import BaseModel, ConfigDict

from dyadtetrade.domain import (
    Account,
    Bar,
    CancelRequest,
    Order,
    OrderRequest,
    Position,
    StrategyState,
    Tick,
    Trade,
)


class EmptyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyRuntime(Protocol):
    @property
    def now(self) -> datetime: ...

    def history(self, symbols: list[str], count: int, fields: list[str]) -> pd.DataFrame: ...

    def current(self, symbol: str) -> Bar | Tick | None: ...

    def get_account(self) -> Account: ...

    def submit_order(self, request: OrderRequest) -> Order: ...

    def cancel_order(self, request: CancelRequest) -> Order | None: ...

    def schedule(self, name: str, delay: timedelta, callback: Callable[[], None]) -> None: ...

    def record(self, values: dict[str, float]) -> None: ...


class StrategyContext:
    def __init__(self, strategy_id: str, runtime: StrategyRuntime) -> None:
        self.strategy_id = strategy_id
        self._runtime = runtime

    @property
    def now(self) -> datetime:
        return self._runtime.now

    def history(self, symbols: list[str], count: int, fields: list[str]) -> pd.DataFrame:
        if count <= 0:
            raise ValueError("history count must be positive")
        return self._runtime.history(symbols, count, fields)

    def current(self, symbol: str) -> Bar | Tick | None:
        return self._runtime.current(symbol)

    def get_cash(self) -> Decimal:
        return self._runtime.get_account().available_cash

    def get_position(self, symbol: str) -> Position | None:
        return self._runtime.get_account().positions.get(symbol)

    def get_positions(self) -> dict[str, Position]:
        return dict(self._runtime.get_account().positions)

    def get_account(self) -> Account:
        return self._runtime.get_account()

    def order(self, request: OrderRequest) -> Order:
        if request.strategy_id not in {"", self.strategy_id}:
            raise ValueError("strategy cannot submit an order for another strategy")
        normalized = OrderRequest(
            symbol=request.symbol,
            direction=request.direction,
            quantity=request.quantity,
            order_type=request.order_type,
            price_type=request.price_type,
            limit_price=request.limit_price,
            strategy_id=self.strategy_id,
            account_id=request.account_id,
            client_order_id=request.client_order_id,
            idempotency_key=request.idempotency_key,
            parent_order_id=request.parent_order_id,
            created_at=self.now,
        )
        return self._runtime.submit_order(normalized)

    def buy(self, symbol: str, quantity: int) -> Order:
        from dyadtetrade.domain import Direction

        return self.order(
            OrderRequest(symbol, Direction.BUY, quantity, strategy_id=self.strategy_id)
        )

    def sell(self, symbol: str, quantity: int) -> Order:
        from dyadtetrade.domain import Direction

        return self.order(
            OrderRequest(symbol, Direction.SELL, quantity, strategy_id=self.strategy_id)
        )

    def order_value(self, symbol: str, value: Decimal) -> Order | None:
        current = self.current(symbol)
        if isinstance(current, Bar):
            price = current.close
        elif current is not None:
            price = current.last_price
        else:
            price = None
        if price is None or price <= 0:
            return None
        quantity = int(value.copy_abs() / price / 100) * 100
        if quantity == 0:
            return None
        return self.buy(symbol, quantity) if value > 0 else self.sell(symbol, quantity)

    def order_target_value(self, symbol: str, target: Decimal) -> Order | None:
        position = self.get_position(symbol)
        current_value = position.market_value if position else Decimal("0")
        difference = target - current_value
        if difference >= 0 or position is None:
            return self.order_value(symbol, difference)
        current = self.current(symbol)
        if isinstance(current, Bar):
            price = current.close
        elif current is not None:
            price = current.last_price
        else:
            return None
        if price <= 0:
            return None
        desired = int(difference.copy_abs() / price / 100) * 100
        sellable = position.available_quantity // 100 * 100
        quantity = min(desired, sellable)
        return self.sell(symbol, quantity) if quantity > 0 else None

    def order_target_percent(self, symbol: str, target: Decimal) -> Order | None:
        if not Decimal("0") <= target <= Decimal("1"):
            raise ValueError("target percent must be between zero and one")
        return self.order_target_value(symbol, self.get_account().total_assets * target)

    def cancel_order(self, order_id: str) -> Order | None:
        return self._runtime.cancel_order(CancelRequest(order_id, requested_at=self.now))

    def schedule(self, name: str, delay: timedelta, callback: Callable[[], None]) -> None:
        self._runtime.schedule(name, delay, callback)

    def record(self, **values: float) -> None:
        self._runtime.record(values)

    def log(self, message: str, *, level: int = logging.INFO) -> None:
        logging.getLogger(f"strategy.{self.strategy_id}").log(
            level, message, extra={"strategy_id": self.strategy_id}
        )


class Strategy:
    name = "base"
    version = "1.0.0"
    description = "Base DyadTetrade strategy"
    parameters_model: type[BaseModel] = EmptyParameters

    def __init__(self, strategy_id: str, parameters: dict[str, Any] | None = None) -> None:
        self.strategy_id = strategy_id
        raw_parameters = dict(parameters or {})
        self.random_seed = int(raw_parameters.pop("random_seed", 0))
        self.parameters = self.parameters_model.model_validate(raw_parameters)
        self.state: dict[str, Any] = {}

    def on_init(self, context: StrategyContext) -> None:  # noqa: B027
        pass

    def on_start(self, context: StrategyContext) -> None:  # noqa: B027
        pass

    def on_bar(self, context: StrategyContext, bar: Bar) -> None:  # noqa: B027
        pass

    def on_tick(self, context: StrategyContext, tick: Tick) -> None:  # noqa: B027
        pass

    def on_timer(self, context: StrategyContext) -> None:  # noqa: B027
        pass

    def on_order(self, context: StrategyContext, order: Order) -> None:  # noqa: B027
        pass

    def on_trade(self, context: StrategyContext, trade: Trade) -> None:  # noqa: B027
        pass

    def on_stop(self, context: StrategyContext) -> None:  # noqa: B027
        pass

    def snapshot(self, at: datetime) -> StrategyState:
        return StrategyState(
            strategy_id=self.strategy_id,
            version=self.version,
            timestamp=at,
            payload=dict(self.state),
            random_seed=self.random_seed,
        )

    def restore(self, state: StrategyState) -> None:
        if state.strategy_id != self.strategy_id or state.version != self.version:
            raise ValueError("strategy state identity or version mismatch")
        self.state = dict(state.payload)
        self.random_seed = state.random_seed
