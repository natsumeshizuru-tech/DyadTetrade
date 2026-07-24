from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from dyadtetrade.domain import Bar, Direction, Instrument, Order, OrderStatus, OrderType
from dyadtetrade.modules.simulation.constraints import (
    ASharePriceLimitPolicy,
    is_conservative_limit_blocked,
    volume_limited_quantity,
)


@dataclass(frozen=True, slots=True)
class MatchResult:
    quantity: int
    price: Decimal | None
    reason: str


@dataclass(frozen=True, slots=True)
class BarMatcher:
    slippage_bps: Decimal = Decimal("0")
    max_volume_participation: Decimal = Decimal("1")
    conservative_limit_board: bool = True
    allow_partial_fills: bool = True
    price_limit_policy: ASharePriceLimitPolicy = ASharePriceLimitPolicy()

    def match(self, order: Order, bar: Bar, instrument: Instrument) -> MatchResult:
        if order.status not in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
            return MatchResult(0, None, "order_not_active")
        if bar.suspended or bar.volume <= 0:
            return MatchResult(0, None, "suspended_or_no_volume")
        if self.conservative_limit_board and is_conservative_limit_blocked(
            bar, order.request.direction, instrument, self.price_limit_policy
        ):
            return MatchResult(0, None, "one_word_limit_board")

        base_price = bar.open
        if order.request.order_type is OrderType.LIMIT:
            limit = order.request.limit_price
            if limit is None:
                return MatchResult(0, None, "missing_limit_price")
            if order.request.direction is Direction.BUY:
                if bar.low > limit:
                    return MatchResult(0, None, "limit_not_reached")
                base_price = min(bar.open, limit)
            else:
                if bar.high < limit:
                    return MatchResult(0, None, "limit_not_reached")
                base_price = max(bar.open, limit)

        direction_multiplier = (
            Decimal("1") if order.request.direction is Direction.BUY else Decimal("-1")
        )
        price = base_price * (
            Decimal("1") + direction_multiplier * self.slippage_bps / Decimal("10000")
        )
        rounding = ROUND_CEILING if order.request.direction is Direction.BUY else ROUND_FLOOR
        price = (price / instrument.price_tick).to_integral_value(
            rounding=rounding
        ) * instrument.price_tick
        quantity = volume_limited_quantity(
            order.remaining_quantity,
            bar,
            self.max_volume_participation,
            instrument.lot_size,
        )
        if not self.allow_partial_fills and quantity < order.remaining_quantity:
            return MatchResult(0, None, "volume_limit")
        return MatchResult(quantity, price, "matched" if quantity else "volume_limit")
