"""Deprecated import facade for the Paper Broker simulator."""

import warnings

from dyadtetrade.modules.simulation.broker import SimulatedBroker

warnings.warn(
    "dyadtetrade.modules.backtest.broker moved to modules.simulation.broker; "
    "the facade will be removed in 0.3.0",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["SimulatedBroker"]
