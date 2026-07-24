"""Deprecated import facade for execution fee simulation."""

import warnings

from dyadtetrade.modules.simulation.commission import AShareCommission, FeeBreakdown, money

warnings.warn(
    "dyadtetrade.modules.backtest.commission moved to modules.simulation.commission; "
    "the facade will be removed in 0.3.0",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["AShareCommission", "FeeBreakdown", "money"]
