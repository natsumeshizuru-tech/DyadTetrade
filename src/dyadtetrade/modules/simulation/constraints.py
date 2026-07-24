from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from dyadtetrade.domain import Bar, Direction, Instrument, InstrumentType, OrderRequest


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    accepted: bool
    code: str = "OK"
    message: str = "accepted"


@dataclass(frozen=True, slots=True)
class ASharePriceLimitPolicy:
    normal_ratio: Decimal = Decimal("0.10")
    st_ratio: Decimal = Decimal("0.05")
    growth_ratio: Decimal = Decimal("0.20")
    beijing_ratio: Decimal = Decimal("0.30")

    def ratio(self, instrument: Instrument) -> Decimal:
        if instrument.price_limit_ratio is not None:
            return instrument.price_limit_ratio
        if instrument.is_st:
            return self.st_ratio
        if instrument.exchange.value == "BJ":
            return self.beijing_ratio
        if instrument.symbol.startswith(("300", "301", "688", "689")):
            return self.growth_ratio
        return self.normal_ratio

    def limits(self, instrument: Instrument, pre_close: Decimal) -> tuple[Decimal, Decimal]:
        ratio = self.ratio(instrument)
        tick = instrument.price_tick
        upper = (pre_close * (Decimal("1") + ratio) / tick).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ) * tick
        lower = (pre_close * (Decimal("1") - ratio) / tick).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ) * tick
        return upper, lower


def default_instrument(symbol: str) -> Instrument:
    from dyadtetrade.domain import Exchange

    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else "UNKNOWN"
    exchange = Exchange(suffix) if suffix in {"SH", "SZ", "BJ"} else Exchange.UNKNOWN
    instrument_type = (
        InstrumentType.ETF if symbol.startswith(("5", "15", "16")) else InstrumentType.STOCK
    )
    return Instrument(symbol, exchange, instrument_type, lot_size=100)


def validate_order_request(
    request: OrderRequest,
    instrument: Instrument,
    *,
    available_cash: Decimal,
    available_quantity: int,
    reference_price: Decimal,
    estimated_fee: Decimal,
    trade_date: date | None = None,
) -> ConstraintResult:
    if (
        trade_date is not None
        and instrument.list_date is not None
        and trade_date < instrument.list_date
    ):
        return ConstraintResult(False, "NOT_LISTED", "instrument is not listed on trade date")
    if (
        trade_date is not None
        and instrument.delist_date is not None
        and trade_date > instrument.delist_date
    ):
        return ConstraintResult(False, "DELISTED", "instrument is delisted on trade date")
    if request.quantity % instrument.lot_size:
        return ConstraintResult(
            False,
            "INVALID_LOT",
            f"quantity must be a multiple of {instrument.lot_size}",
        )
    if request.limit_price is not None and request.limit_price % instrument.price_tick:
        return ConstraintResult(
            False,
            "INVALID_PRICE_TICK",
            f"limit price must be a multiple of {instrument.price_tick}",
        )
    if request.direction is Direction.BUY:
        required = reference_price * request.quantity + estimated_fee
        if available_cash < required:
            return ConstraintResult(False, "INSUFFICIENT_CASH", "available cash is insufficient")
    elif available_quantity < request.quantity:
        return ConstraintResult(
            False, "INSUFFICIENT_POSITION", "available position is insufficient"
        )
    return ConstraintResult(True)


def volume_limited_quantity(requested: int, bar: Bar, participation: Decimal, lot_size: int) -> int:
    capacity = (
        int(
            (Decimal(bar.volume) * participation / lot_size).to_integral_value(rounding=ROUND_FLOOR)
        )
        * lot_size
    )
    return min(requested, max(capacity, 0))


def is_conservative_limit_blocked(
    bar: Bar,
    direction: Direction,
    instrument: Instrument,
    policy: ASharePriceLimitPolicy,
) -> bool:
    limit_up, limit_down = bar.limit_up, bar.limit_down
    if bar.pre_close is not None and (limit_up is None or limit_down is None):
        calculated_up, calculated_down = policy.limits(instrument, bar.pre_close)
        limit_up = limit_up or calculated_up
        limit_down = limit_down or calculated_down
    if direction is Direction.BUY and limit_up is not None:
        return bar.open >= limit_up and bar.low >= limit_up
    if direction is Direction.SELL and limit_down is not None:
        return bar.open <= limit_down and bar.high <= limit_down
    return False
