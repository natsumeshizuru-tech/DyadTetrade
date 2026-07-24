"""Deprecated import facade for bar-based paper matching."""

import warnings

from dyadtetrade.modules.simulation.matcher import BarMatcher, MatchResult

warnings.warn(
    "dyadtetrade.modules.backtest.matcher moved to modules.simulation.matcher; "
    "the facade will be removed in 0.3.0",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["BarMatcher", "MatchResult"]
