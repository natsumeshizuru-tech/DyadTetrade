from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd

from dyadtetrade.domain import Order, Trade


@dataclass(frozen=True, slots=True)
class DailyEquity:
    date: str
    cash: Decimal
    positions_value: Decimal
    total_assets: Decimal
    return_value: Decimal
    drawdown: Decimal
    net_value: Decimal = Decimal("1")
    benchmark_net_value: Decimal | None = None
    relative_net_value: Decimal | None = None


@dataclass(slots=True)
class BacktestResult:
    run_id: str
    config_snapshot: dict[str, Any]
    data_fingerprint: str
    strategy_version: str
    orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    daily_equity: list[DailyEquity] = field(default_factory=list)
    records: list[dict[str, float]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    final_positions: dict[str, Decimal] = field(default_factory=dict)

    def equity_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(item) for item in self.daily_equity])

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config": self.config_snapshot,
            "data_fingerprint": self.data_fingerprint,
            "strategy_version": self.strategy_version,
            "order_count": len(self.orders),
            "trade_count": len(self.trades),
            "position_count": len(self.final_positions),
            "final_positions": {key: str(value) for key, value in self.final_positions.items()},
            "metrics": self.metrics,
        }
