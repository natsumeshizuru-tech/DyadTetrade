"""Execution simulation primitives shared by Paper Broker and legacy backtests."""

from dyadtetrade.modules.simulation.broker import SimulatedBroker
from dyadtetrade.modules.simulation.commission import AShareCommission, FeeBreakdown
from dyadtetrade.modules.simulation.constraints import (
    ASharePriceLimitPolicy,
    ConstraintResult,
    default_instrument,
    validate_order_request,
)
from dyadtetrade.modules.simulation.matcher import BarMatcher, MatchResult

__all__ = [
    "AShareCommission",
    "ASharePriceLimitPolicy",
    "BarMatcher",
    "ConstraintResult",
    "FeeBreakdown",
    "MatchResult",
    "SimulatedBroker",
    "default_instrument",
    "validate_order_request",
]
