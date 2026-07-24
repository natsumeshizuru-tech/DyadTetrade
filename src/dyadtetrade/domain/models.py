from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from dyadtetrade.domain.enums import (
    Direction,
    Exchange,
    InstrumentType,
    Interval,
    OrderStatus,
    OrderType,
    PriceType,
    TradingStatus,
)
from dyadtetrade.domain.orders import assert_transition

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    exchange: Exchange
    instrument_type: InstrumentType
    name: str = ""
    lot_size: int = 100
    price_tick: Decimal = Decimal("0.01")
    price_decimals: int = 2
    quantity_decimals: int = 0
    currency: str = "CNY"
    money_decimals: int = 2
    list_date: date | None = None
    delist_date: date | None = None
    industry: str | None = None
    is_st: bool = False
    price_limit_ratio: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timestamp: datetime
    interval: Interval
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: Decimal = ZERO
    pre_close: Decimal | None = None
    limit_up: Decimal | None = None
    limit_down: Decimal | None = None
    suspended: bool = False
    adjustment: str = "none"
    source: str = "dyadtetrade"
    is_final: bool = True
    received_at: datetime | None = None
    updated_at: datetime | None = None
    quality_status: str = "valid"

    def __post_init__(self) -> None:
        if self.volume < 0 or self.amount < 0:
            raise ValueError("bar volume and amount must be non-negative")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("bar prices must be positive")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("bar high violates OHLC relationship")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("bar low violates OHLC relationship")


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str
    timestamp: datetime
    last_price: Decimal
    volume: int = 0
    amount: Decimal = ZERO
    bid_price: tuple[Decimal, ...] = ()
    ask_price: tuple[Decimal, ...] = ()
    bid_volume: tuple[int, ...] = ()
    ask_volume: tuple[int, ...] = ()
    trading_status: TradingStatus = TradingStatus.UNKNOWN
    source: str = "dyadtetrade"


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    timestamp: datetime
    bid_price: Decimal | None
    ask_price: Decimal | None
    bid_volume: int = 0
    ask_volume: int = 0
    last_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Signal:
    strategy_id: str
    symbol: str
    timestamp: datetime
    direction: Direction
    strength: Decimal = Decimal("1")
    reason: str = ""
    visible_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.visible_at is not None and self.visible_at > self.timestamp:
            raise ValueError("signal cannot use information visible after signal timestamp")


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    direction: Direction
    quantity: int
    order_type: OrderType = OrderType.MARKET
    price_type: PriceType = PriceType.NEXT_BAR_OPEN
    limit_price: Decimal | None = None
    strategy_id: str = ""
    account_id: str = "paper"
    client_order_id: str = field(default_factory=lambda: uuid4().hex)
    idempotency_key: str = field(default_factory=lambda: uuid4().hex)
    parent_order_id: str | None = None
    created_at: datetime | None = None
    strategy_release_id: str | None = None
    signal_id: str | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order requires limit_price")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    request: OrderRequest
    created_at: datetime
    run_id: str | None = None
    source: str = "strategy"


@dataclass(frozen=True, slots=True)
class CancelRequest:
    order_id: str
    account_id: str = "paper"
    requested_at: datetime | None = None


@dataclass(slots=True)
class Order:
    order_id: str
    request: OrderRequest
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: int = 0
    average_fill_price: Decimal = ZERO
    frozen_cash: Decimal = ZERO
    frozen_quantity: int = 0
    reject_reason: str | None = None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    broker_order_id: str | None = None
    qmt_order_id: int | None = None
    order_sysid: str | None = None

    @property
    def remaining_quantity(self) -> int:
        return self.request.quantity - self.filled_quantity

    def transition(self, target: OrderStatus, at: datetime | None = None) -> None:
        assert_transition(self.status, target)
        self.status = target
        self.updated_at = at

    def apply_fill(self, quantity: int, price: Decimal, at: datetime) -> None:
        if quantity <= 0 or quantity > self.remaining_quantity:
            raise ValueError("fill quantity exceeds remaining order quantity")
        notional_before = self.average_fill_price * self.filled_quantity
        self.filled_quantity += quantity
        self.average_fill_price = (notional_before + price * quantity) / self.filled_quantity
        target = (
            OrderStatus.FILLED
            if self.filled_quantity == self.request.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        self.transition(target, at)


@dataclass(frozen=True, slots=True)
class Trade:
    trade_id: str
    order_id: str
    symbol: str
    direction: Direction
    quantity: int
    price: Decimal
    timestamp: datetime
    commission: Decimal = ZERO
    tax: Decimal = ZERO
    transfer_fee: Decimal = ZERO
    broker_reported_fee: Decimal | None = None
    fee_source: str = "component_sum"
    fee_breakdown_complete: bool = True
    slippage: Decimal = ZERO
    execution_mode: str = "backtest"
    account_id: str = "backtest"
    strategy_id: str = ""
    run_id: str | None = None
    source: str = "backtest_match"
    is_simulated: bool = True
    broker_order_id: str | None = None
    qmt_order_id: int | None = None
    order_sysid: str | None = None
    broker_trade_id: str | None = None
    broker_reported_at: datetime | None = None

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity

    @property
    def total_fees(self) -> Decimal:
        if self.broker_reported_fee is not None:
            return self.broker_reported_fee
        return self.commission + self.tax + self.transfer_fee


@dataclass(frozen=True, slots=True)
class BrokerSettlement:
    settlement_id: str
    account_id: str
    broker_trade_id: str
    broker_order_id: str
    symbol: str
    direction: Direction
    quantity: int
    price: Decimal
    notional: Decimal
    commission: Decimal
    tax: Decimal
    transfer_fee: Decimal
    settlement_amount: Decimal
    broker_reported_at: datetime
    source: str


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: int = 0
    available_quantity: int = 0
    frozen_quantity: int = 0
    today_bought: int = 0
    average_cost: Decimal = ZERO
    last_price: Decimal = ZERO

    @property
    def market_value(self) -> Decimal:
        return self.last_price * self.quantity


@dataclass(slots=True)
class Account:
    account_id: str
    cash: Decimal
    available_cash: Decimal
    frozen_cash: Decimal = ZERO
    accrued: Decimal = ZERO
    positions: dict[str, Position] = field(default_factory=dict)
    reported_positions_value: Decimal | None = None
    reported_total_assets: Decimal | None = None

    @property
    def positions_value(self) -> Decimal:
        if self.reported_positions_value is not None:
            return self.reported_positions_value
        return sum((position.market_value for position in self.positions.values()), ZERO)

    @property
    def total_assets(self) -> Decimal:
        if self.reported_total_assets is not None:
            return self.reported_total_assets
        return self.cash + self.positions_value + self.accrued

    def assert_identity(self, expected_total: Decimal | None = None) -> None:
        if self.cash != self.available_cash + self.frozen_cash:
            raise ArithmeticError("cash identity violated: cash != available + frozen")
        if expected_total is not None and abs(self.total_assets - expected_total) > Decimal("0.01"):
            raise ArithmeticError("asset identity violated")


@dataclass(slots=True)
class Portfolio:
    portfolio_id: str
    account: Account
    target_weights: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: str
    ex_date: date
    action_type: str
    cash_dividend_per_share: Decimal = ZERO
    split_ratio: Decimal = Decimal("1")
    announced_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RiskResult:
    accepted: bool
    code: str
    message: str
    checked_at: datetime
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategyState:
    strategy_id: str
    version: str
    timestamp: datetime
    payload: dict[str, Any]
    random_seed: int
