from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Side = Literal["buy", "sell"]
OrderStatus = Literal["pending", "filled", "rejected"]


@dataclass(frozen=True, slots=True)
class HistoricalBacktestSettings:
    initial_cash: Decimal = Decimal("1000000")
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    stamp_duty_rate: Decimal = Decimal("0.0005")
    transfer_fee_rate: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    enforce_t1: bool = True
    buy_lot_size: int = 100
    limit_ratio: Decimal = Decimal("0.10")


@dataclass(slots=True)
class HistoricalOrder:
    order_id: int
    symbol: str
    side: Side
    quantity: int
    submitted_ts: int
    status: OrderStatus = "pending"
    reason: str | None = None
    filled_ts: int | None = None
    filled_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class HistoricalTrade:
    trade_id: int
    order_id: int
    symbol: str
    side: Side
    quantity: int
    price: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    ts: int


@dataclass(slots=True)
class HistoricalPosition:
    symbol: str
    quantity: int = 0
    available: int = 0
    today_buys: int = 0
    average_cost: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    ts: int
    cash: Decimal
    position_value: Decimal
    total_assets: Decimal


@dataclass(frozen=True, slots=True)
class BacktestRunResult:
    run_id: str
    start_ts: int
    end_ts: int
    bars_processed: int
    final_cash: Decimal
    final_assets: Decimal
    orders: tuple[HistoricalOrder, ...]
    trades: tuple[HistoricalTrade, ...]
    equity: tuple[EquityPoint, ...]
    result_database: str
