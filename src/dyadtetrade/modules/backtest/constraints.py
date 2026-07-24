"""Deprecated import facade for A-share execution constraints."""

import warnings

from dyadtetrade.modules.simulation.constraints import (
    ASharePriceLimitPolicy,
    ConstraintResult,
    default_instrument,
    is_conservative_limit_blocked,
    validate_order_request,
    volume_limited_quantity,
)

warnings.warn(
    "dyadtetrade.modules.backtest.constraints moved to modules.simulation.constraints; "
    "the facade will be removed in 0.3.0",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ASharePriceLimitPolicy",
    "ConstraintResult",
    "default_instrument",
    "is_conservative_limit_blocked",
    "validate_order_request",
    "volume_limited_quantity",
]
