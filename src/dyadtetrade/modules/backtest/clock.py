from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class BacktestClock:
    current: datetime

    def advance(self, timestamp: datetime) -> None:
        if timestamp < self.current:
            raise ValueError("backtest clock cannot move backwards")
        self.current = timestamp
