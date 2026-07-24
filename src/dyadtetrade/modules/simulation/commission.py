from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from dyadtetrade.domain import Direction

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.stamp_duty + self.transfer_fee


@dataclass(frozen=True, slots=True)
class AShareCommission:
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    stamp_duty_rate: Decimal = Decimal("0.0005")
    transfer_fee_rate: Decimal = Decimal("0")

    def calculate(self, direction: Direction, price: Decimal, quantity: int) -> FeeBreakdown:
        if price <= 0 or quantity <= 0:
            raise ValueError("fee input price and quantity must be positive")
        notional = price * quantity
        commission = money(max(notional * self.commission_rate, self.minimum_commission))
        stamp_duty = (
            money(notional * self.stamp_duty_rate) if direction is Direction.SELL else Decimal("0")
        )
        transfer_fee = money(notional * self.transfer_fee_rate)
        result = FeeBreakdown(commission, stamp_duty, transfer_fee)
        if result.total < 0:
            raise ArithmeticError("fees must never be negative")
        return result
