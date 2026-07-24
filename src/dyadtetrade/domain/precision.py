from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True, slots=True)
class SecurityPrecision:
    price_tick: Decimal = Decimal("0.01")
    price_decimals: int = 2
    quantity_decimals: int = 0
    lot_size: int = 100
    currency: str = "CNY"
    money_decimals: int = 2


def decimal_value(value: object) -> Decimal:
    """Convert external numeric values without passing through binary float."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def tick_decimals(tick: Decimal) -> int:
    normalized = tick.normalize()
    exponent = normalized.as_tuple().exponent
    return max(-exponent, 0) if isinstance(exponent, int) else 0


def quantize_price(value: object, tick: Decimal, *, strict: bool = True) -> Decimal:
    price = decimal_value(value)
    if tick <= 0:
        raise ValueError("price_tick must be positive")
    units = price / tick
    integral = units.to_integral_value(rounding=ROUND_HALF_UP)
    if strict and units != integral:
        raise ValueError(f"price {price} is not an integer multiple of tick {tick}")
    return (integral * tick).quantize(tick)


def quantize_money(value: object, decimals: int = 2) -> Decimal:
    quantum = Decimal(1).scaleb(-decimals)
    return decimal_value(value).quantize(quantum, rounding=ROUND_HALF_UP)


def api_decimal(value: object) -> str:
    return format(decimal_value(value), "f")
